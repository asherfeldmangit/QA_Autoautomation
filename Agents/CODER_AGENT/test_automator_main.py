import json
from pathlib import Path
from dotenv import load_dotenv  # noqa: WPS433 – runtime import required before openai
from openai import OpenAI


class CoderAgentShell:
    """LLM-powered agent that converts structured JSON test cases into executable **JavaScript Playwright** test code.
    The class is deliberately lightweight so that it can be imported by an external orchestrator and
    repeatedly invoked with reviewer feedback until all tests pass.
    """

    def __init__(self, test_case_json_path: str, output_dir: str, model: str = "gpt-4o-mini"):
        """Initialise the agent.

        Args:
            test_case_json_path: Path to the JSON file produced by `TestCaseGeneratorAgent`.
            output_dir: Directory where the generated python test file will be written.
            model: Any chat-completion capable OpenAI model name.
        """
        load_dotenv(override=True)  # ensures OPENAI_API_KEY is picked up from the user environment
        self.openai = OpenAI()

        self.test_case_json_path = Path(test_case_json_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.test_cases: dict | None = None

    # ---------------------------------------------------------------------
    # Public helpers
    # ---------------------------------------------------------------------
    def run(self) -> str:
        """High-level convenience wrapper.

        Loads the JSON test cases, generates test code and writes it to disk. Returns the path of the
        generated file so that downstream components (e.g. the reviewer agent) can consume it.
        """
        self._load_test_cases()
        code_str = self._generate_code()
        return str(self._save_code(code_str))

    def improve_code(self, feedback: str) -> str:
        """Iteratively refine the previously generated code based on reviewer feedback.

        Args:
            feedback: Human-readable feedback string returned by the `CodeReviewerAgent`.

        Returns:
            Absolute path to the updated python test file.
        """
        prev_code_path = self.output_dir / "test_generated.spec.js"
        prev_code = prev_code_path.read_text() if prev_code_path.exists() else ""

        prompt = (
            "You are an expert QA automation engineer. Improve the following Playwright test code (JavaScript) so that it "
            "addresses the feedback below and passes all tests. Return *only* the updated JavaScript code "
            "— no markdown or commentary.\n\n"  # noqa: E501
            f"FEEDBACK:\n{feedback}\n\nPREVIOUS_CODE:\n{prev_code}"
        )

        response = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        improved_code = response.choices[0].message.content
        return str(self._save_code(improved_code))

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _load_test_cases(self) -> None:
        with self.test_case_json_path.open() as f:
            self.test_cases = json.load(f)

    def _build_prompt(self) -> str:
        json_blob = json.dumps(self.test_cases, indent=2)

        # ------------------------------------------------------------------
        # Gather additional context from the Playwright helper scripts
        # ------------------------------------------------------------------
        project_root = Path(__file__).resolve().parent.parent.parent
        context_dir = project_root / "context_playwright"

        context_parts: list[str] = []
        if context_dir.exists():
            for file_path in context_dir.glob("**/*"):
                if file_path.is_file():
                    try:
                        file_content = file_path.read_text()
                    except Exception:
                        # Any unreadable file is skipped to avoid breaking the prompt
                        file_content = ""
                    context_parts.append(f"FILE: {file_path.name}\\n{file_content}")

        context_blob = "\n\n".join(context_parts)

        return (
            "You are an experienced JavaScript test automation developer specialising in Playwright. "
            "Write Playwright test code (JavaScript) that fulfils the following test cases (provided as JSON). "
            "Follow the *exact same structure, fixtures, naming conventions and helper function usage* demonstrated in the `CONTEXT_PLAYWRIGHT_FILES` examples so that the newly generated tests are consistent with the existing suite. "
            "For HTTP requests, use Playwright's built-in APIRequestContext (via the request fixture) or the helper classes provided in the context. Avoid any non-standard dependencies. "
            "Respond with *pure JavaScript code only* – no markdown fences or additional explanation.\n\n"  # noqa: E501
            f"TEST_CASES_JSON:\n{json_blob}\n\n"
            f"CONTEXT_PLAYWRIGHT_FILES:\n{context_blob}"
        )

    def _generate_code(self) -> str:
        prompt = self._build_prompt()
        response = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def _save_code(self, code_str: str, file_name: str = "test_generated.spec.js"):
        """Persist the generated python code to *output_dir / file_name* and return that `Path`."""
        output_path = Path(self.output_dir) / file_name
        output_path.write_text(code_str)
        print(f"Test code written to {output_path}")
        return output_path
