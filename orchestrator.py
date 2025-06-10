from __future__ import annotations

from pathlib import Path

from Agents.TC_GENERATOR_AGENT.test_writer_main import TestCaseGeneratorAgent
from Agents.CODER_AGENT.test_automator_main import CoderAgentShell
from Agents.CODE_REVIEWER_AGENT.test_reviewer_main import CodeReviewerAgent


class Orchestrator:
    """High-level controller that wires the individual agents together into a feedback loop."""

    def __init__(
        self,
        xml_path: str | Path,
        work_dir: str | Path = "artifacts",
        model: str = "gpt-4o-mini",
        max_review_cycles: int = 3,
    ) -> None:
        """Create a new orchestrator instance.

        Args:
            xml_path: Path to the **TestRail XML** export that acts as the single source of truth for the tests.
            work_dir: Directory where *all* intermediate artefacts (JSON, generated code, runtime logs) will be stored.
            model: OpenAI model ID passed on to the individual agents. Allows you to override it from the CLI.
            max_review_cycles: Safety valve – if the Reviewer agent still fails after *n* feedback loops, the run aborts.
        """
        self.xml_path = Path(xml_path)
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(exist_ok=True, parents=True)
        self.model = model
        self.max_review_cycles = max_review_cycles

        # Derived paths
        self.json_path = self.work_dir / "generated_test_cases.json"
        self.code_dir = self.work_dir / "generated_tests"
        self.code_dir.mkdir(exist_ok=True)

        # Agents
        self.tc_generator = TestCaseGeneratorAgent(
            xml_path=str(self.xml_path),
            output_json_path=str(self.json_path),
            model=self.model,
        )
        self.coder = CoderAgentShell(
            test_case_json_path=str(self.json_path),
            output_dir=str(self.code_dir),
            model=self.model,
        )
        self.reviewer = CodeReviewerAgent(
            # The CoderAgent now produces JavaScript Playwright tests (`*.spec.js`).
            # Point the reviewer to the correct file extension so that it can run the right runtime check.
            test_file_path=self.code_dir / "test_generated.spec.js",
            model=self.model,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Execute the full agent pipeline."""
        print("[Orchestrator] Generating test cases from XML …")
        self.tc_generator.run()

        print("[Orchestrator] Generating test code from JSON …")
        self.coder.run()

        for cycle in range(1, self.max_review_cycles + 1):
            print(f"[Orchestrator] Review cycle {cycle}/{self.max_review_cycles} …")
            passed, feedback = self.reviewer.review_code()
            if passed:
                print("[Orchestrator] ✅ Test suite passed review!")
                break

            # Surface the exact failure details before handing the feedback back to the Coder agent.
            print("[Orchestrator] ❌ Review failed – feeding feedback back to coder agent …")
            print("[Orchestrator] Reviewer feedback:\n" + feedback)
            self.coder.improve_code(feedback)
        else:
            print("[Orchestrator] 🚨 Maximum review cycles reached – manual intervention required.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="End-to-end QA Automation Orchestrator")
    parser.add_argument("xml", help="Path to a TestRail XML export containing test cases")
    parser.add_argument(
        "--work-dir",
        default="artifacts",
        help="Directory where intermediate JSON/code artefacts will be stored",
    )
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name")
    parser.add_argument(
        "--max-review-cycles",
        type=int,
        default=3,
        help="How many times the reviewer may return feedback before giving up",
    )

    args = parser.parse_args()
    Orchestrator(
        xml_path=args.xml,
        work_dir=args.work_dir,
        model=args.model,
        max_review_cycles=args.max_review_cycles,
    ).run() 