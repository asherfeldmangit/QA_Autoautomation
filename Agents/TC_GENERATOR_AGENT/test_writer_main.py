class TestCaseGeneratorAgent:
    def __init__(self, xml_path, output_json_path, model="gpt-4.1-mini"):
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

    def load_json_to_dict(self, file_path):
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

    def parse_xml(self):
        self.context_tc_xml_tree = self.ET.parse(self.xml_path)
        self.context_tc_xml = self.ET.tostring(self.context_tc_xml_tree.getroot(), encoding='unicode')

    def build_prompt(self):
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

    def save_test_cases(self, test_cases):
        import json
        with open(self.output_json_path, "w") as outfile:
            json.dump(test_cases, outfile, indent=2)
        print(f"JSON written to {self.output_json_path}")

    def run(self):
        self.parse_xml()
        test_cases = self.generate_test_cases()
        if test_cases is not None:
            self.save_test_cases(test_cases)
