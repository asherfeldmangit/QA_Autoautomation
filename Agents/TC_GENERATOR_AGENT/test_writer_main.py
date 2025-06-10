class TestCaseGeneratorAgent:
    """LLM-powered agent that converts **TestRail XML** test cases into a structured JSON schema.

    A thin wrapper around the OpenAI chat-completion API. The generated JSON payload is later consumed by
    the :class:`Agents.CODER_AGENT.CoderAgentShell` to translate the test cases into executable code.

    The agent follows a simple three-step workflow:

    1. **Parse XML** – read the TestRail export and keep it as a Unicode string.
    2. **Prompt LLM** – send the raw XML plus carefully crafted instructions to the model.
    3. **Persist JSON** – write the model response to *output_json_path*.

    All heavy lifting is off-loaded to the LLM; the class merely orchestrates IO.
    """

    def __init__(self, xml_path: str, output_json_path: str, model: str = "gpt-4.1-mini"):
        """Instantiate the agent.

        Args:
            xml_path: Absolute or relative path to a TestRail **XML** export.
            output_json_path: Destination file where the generated JSON test cases will be written.
            model: OpenAI chat-completion model name. Defaults to the light-weight *gpt-4.1-mini*.
        """
        from dotenv import load_dotenv
        from openai import OpenAI
        import xml.etree.ElementTree as ET
        load_dotenv(override=True)
        self.openai = OpenAI()
        self.xml_path = xml_path
        self.output_json_path = output_json_path
        self.model = model
        self.ET = ET
        self.context_tc_xml_tree = None
        self.context_tc_xml = None

    def load_json_to_dict(self, file_path: str):
        """Utility helper to load *any* JSON file into a Python ``dict``.

        Primarily used for debugging or offline inspection – **not** part of the main run pipeline.

        Args:
            file_path: Path to the JSON file to load.

        Returns:
            The decoded Python dictionary or *None* if the file does not exist / is invalid JSON.
        """
        import json
        try:
            with open(file_path, 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            print(f"File not found: {file_path}")
            return None
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
            return None

    def parse_xml(self) -> None:
        """Read the TestRail XML file from *xml_path* into :pyattr:`context_tc_xml`."""
        self.context_tc_xml_tree = self.ET.parse(self.xml_path)
        self.context_tc_xml = self.ET.tostring(self.context_tc_xml_tree.getroot(), encoding='unicode')

    def build_prompt(self) -> str:
        """Construct the **single** prompt sent to the LLM.

        The prompt contains both the *system* instructions and the raw XML payload. It purposefully ends with
        the directive *"Response should be pure JSON"* so that the model does not wrap the output in Markdown
        fences or informal prose.

        Returns:
            Fully-formed prompt string ready to be passed to :pyfunc:`openai.chat.completions.create`.
        """
        prompt = """
You are an experienced QA Engineer. Your job is to write test cases for API endpoints.
You will receive an XML file (exported from testrail) that contains the test cases for an API endpoint.
You will need to convert these XML cases into a uniform text format that can be used to automate the test cases.
The test cases should be written in a way that is easy to understand and easy to automate. JSON format with meaningful key names.
If there are additional test cases that are not covered by the XML file, you should add them to the test cases.
Response Should be pure JSON, no other text, no markdown
"""
        prompt += f"TC_XML_FILE: {self.context_tc_xml}"
        return prompt

    def generate_test_cases(self):
        """Call the LLM and return the decoded JSON test cases.

        Returns:
            A ``dict`` representing the JSON response or *None* if the response could not be parsed.
        """
        import json
        prompt = self.build_prompt()
        response = self.openai.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}]
        )
        response_content = response.choices[0].message.content
        try:
            response_json = json.loads(response_content)
            return response_json
        except json.JSONDecodeError as e:
            print(f"Response is not valid JSON: {e}\nResponse content was:\n{response_content}")
            return None

    def save_test_cases(self, test_cases: dict) -> None:
        """Persist *test_cases* to :pyattr:`output_json_path` on disk.

        Args:
            test_cases: The JSON-serialisable test case dictionary returned by the LLM.
        """
        import json
        with open(self.output_json_path, "w") as outfile:
            json.dump(test_cases, outfile, indent=2)
        print(f"JSON written to {self.output_json_path}")

    def run(self) -> None:
        """Execute the full agent workflow (parse → generate → save)."""
        self.parse_xml()
        test_cases = self.generate_test_cases()
        if test_cases is not None:
            self.save_test_cases(test_cases)
