import json
import re
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
        code_str = self._sanitize_code(code_str)
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
        improved_code = self._sanitize_code(improved_code)
        return str(self._save_code(improved_code))

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _load_test_cases(self) -> None:
        """Populate :pyattr:`test_cases` from the JSON blob on disk.

        The method is intentionally *private* because it mutates internal state without returning a value.
        It is always followed by a call to :meth:`_generate_code` in the public :meth:`run` wrapper.
        """
        with self.test_case_json_path.open() as f:
            self.test_cases = json.load(f)

    def _build_prompt(self) -> str:
        """Craft the prompt that instructs the LLM to emit Playwright tests.

        Besides the `TEST_CASES_JSON`, the prompt also injects **selected** helper files from the
        `context_playwright/` directory so that the generated code is stylistically aligned with the
        existing suite. Only small, *relevant* files are included to keep token usage reasonable.

        Returns:
            The fully assembled prompt ready to be passed to OpenAI.
        """
        json_blob = json.dumps(self.test_cases, indent=2)

        # ------------------------------------------------------------------
        # Locate the Playwright project context directory.
        # The folder may live directly under the project root *or* inside a
        # nested `context/` folder depending on how the repository is laid out.
        # We resolve the first path that exists to remain backwards compatible
        # with both structures.
        # ------------------------------------------------------------------
        candidate_dirs = [
            Path(__file__).resolve().parent.parent.parent / "context_playwright",
            Path(__file__).resolve().parent.parent.parent / "context" / "context_playwright",
        ]

        context_dir = next((d for d in candidate_dirs if d.exists()), None)

        # --------------------------------------------------------------
        # Selectively import only *key* Playwright helper files to keep the
        # prompt size manageable. We purposefully exclude entire test suites
        # or heavyweight files that do not aid code generation.
        # --------------------------------------------------------------
        context_parts: list[str] = []
        if context_dir:  # pragma: no branch – defensive, should usually exist
            allowed_base_dirs = {"utils", "globals", "locators"}
            always_include_files = {"playwright.config.js"}

            for file_path in context_dir.rglob("*"):
                if not file_path.is_file():
                    continue

                rel_parts = file_path.relative_to(context_dir).parts

                # Decide whether to include this file.
                include = (
                    rel_parts[0] in allowed_base_dirs  # e.g. utils/..., globals/...
                    or file_path.name in always_include_files
                )

                # Additionally filter by file size to avoid extremely large files (>30 KB)
                include = include and file_path.stat().st_size < 30_000

                if not include:
                    continue

                try:
                    file_content = file_path.read_text(encoding="utf-8", errors="ignore")
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
        """Send the prompt to the LLM and return the raw JavaScript code string."""

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

    # ---------------------------------------------------------------------
    # Code post-processing helpers
    # ---------------------------------------------------------------------
    @staticmethod
    def _sanitize_code(code_str: str) -> str:
        """Perform lightweight post-processing to ensure syntactically valid JS.

        Particularly, replace invalid placeholder assignments like:

            const foo = /* retrieve or define foo */;

        with a string literal stub so that the file remains valid JavaScript.
        """

        # Matches "= /* ... */;" (optionally with whitespace around the equals sign)
        placeholder_pattern = re.compile(r"=\s*/\*.*?\*/;", re.DOTALL)
        return placeholder_pattern.sub("= '';// TODO: provide value;", code_str)
