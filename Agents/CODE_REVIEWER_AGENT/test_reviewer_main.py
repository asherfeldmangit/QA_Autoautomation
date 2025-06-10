from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv  # noqa: WPS433 – runtime import before openai
from openai import OpenAI


class CodeReviewerAgent:
    """Agent responsible for validating and providing feedback on generated test code.

    The current implementation focuses on *running* the generated test suite via **pytest** and
    relaying any failures back to the orchestrator. It can be easily extended to include LLM-based
    static code review if desired.
    """

    def __init__(self, test_file_path: str | Path, model: str = "gpt-4o-mini"):
        self.test_file_path = Path(test_file_path)
        self.working_dir = self.test_file_path.parent

        # LLM is optional for the MVP but we initialise it for future use
        load_dotenv(override=True)
        self.openai = OpenAI()
        self.model = model

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def review_code(self) -> tuple[bool, str]:
        """Run the tests (Python or JavaScript) and return *(passed, feedback)*.

        The method automatically detects whether the *generated* tests are a Python
        `pytest` suite or a JavaScript **Playwright** spec (\*.spec.js / \*.spec.ts)
        and executes the appropriate runtime command. If the runtime phase passes,
        an optional LLM-powered static code review is performed to catch stylistic
        or best-practice issues that do not show up at execution time.
        """

        # ------------------------------------------------------------------
        # Runtime execution
        # ------------------------------------------------------------------
        if self.test_file_path.suffix in {".js", ".ts"}:
            passed, runtime_feedback = self._run_playwright()
        else:
            passed, runtime_feedback = self._run_pytest()

        # ------------------------------------------------------------------
        # Optional static review via LLM (only if runtime tests succeeded)
        # ------------------------------------------------------------------
        static_feedback = ""
        if passed:
            try:
                static_feedback = self._static_js_review()
            except Exception as exc:  # noqa: WPS429 – broad except is fine for non-critical path
                # We never want the static review step to break the pipeline, so we
                # swallow any exception and just append a minimal error notice.
                static_feedback = f"[Static review failed: {exc}]"

        # Compose final feedback payload
        combined_feedback = runtime_feedback
        if static_feedback:
            combined_feedback += "\n\n[Static Review]\n" + static_feedback

        return passed, combined_feedback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_pytest(self) -> tuple[bool, str]:
        """Execute `pytest` in a subprocess and capture output."""
        try:
            subprocess.check_output(
                [sys.executable, "-m", "pytest", str(self.working_dir)],
                stderr=subprocess.STDOUT,
            )
            return True, "All Python tests passed successfully."
        except subprocess.CalledProcessError as exc:  # pragma: no cover – we just capture output
            return False, exc.output.decode()

    def _run_playwright(self) -> tuple[bool, str]:
        """Execute JavaScript Playwright tests via `npx playwright test` and capture output."""
        cmd = [
            "npx",
            "--yes",  # ensures non-interactive execution
            "playwright",
            "test",
            str(self.test_file_path),
            "--reporter",
            "line",
        ]

        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT, cwd=self.working_dir)
            return True, "All Playwright tests passed successfully."
        except subprocess.CalledProcessError as exc:  # pragma: no cover – we just capture output
            return False, exc.output.decode()

    # ------------------------------------------------------------------
    # LLM static review helpers
    # ------------------------------------------------------------------
    def _static_js_review(self) -> str:
        """Perform an LLM-based static review of the generated JS test file.

        The prompt is enriched with the existing Playwright helper files so that the
        model can judge stylistic and architectural consistency.
        """

        context_blob = self._collect_context_playwright()
        generated_code = self.test_file_path.read_text()

        prompt = (
            "You are a *senior JavaScript/TypeScript QA engineer* specialising in Playwright. "
            "Review the following Playwright test code for correctness, maintainability and adherence "
            "to best practices **given the project context**. Provide concise, actionable feedback.\n\n"  # noqa: E501
            f"GENERATED_TEST_CODE:\n{generated_code}\n\n"
            f"CONTEXT_PLAYWRIGHT_FILES:\n{context_blob}"
        )

        response = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    def _collect_context_playwright(self) -> str:
        """Aggregate the raw content of files in the `context_playwright` directory."""
        project_root = Path(__file__).resolve().parent.parent.parent

        # Support both `<root>/context_playwright` and `<root>/context/context_playwright` layouts.
        candidate_dirs = [
            project_root / "context_playwright",
            project_root / "context" / "context_playwright",
        ]

        context_dir = next((d for d in candidate_dirs if d.exists()), None)

        context_parts: list[str] = []
        if context_dir:  # pragma: no branch – defensive
            allowed_base_dirs = {"utils", "globals", "locators"}
            always_include_files = {"playwright.config.js"}

            for file_path in context_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                rel_parts = file_path.relative_to(context_dir).parts

                include = (
                    rel_parts[0] in allowed_base_dirs
                    or file_path.name in always_include_files
                )

                include = include and file_path.stat().st_size < 30_000

                if not include:
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    content = ""

                context_parts.append(f"FILE: {file_path.name}\\n{content}")

        return "\n\n".join(context_parts) 