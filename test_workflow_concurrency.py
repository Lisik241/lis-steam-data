import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
WRITING_WORKFLOWS = (
    REPO_ROOT / ".github/workflows/update-steam-library.yml",
    REPO_ROOT / ".github/workflows/scan-steam-giveaways.yml",
)
SHARED_WRITER_CONCURRENCY = re.compile(
    r"(?m)^concurrency:\n  group: steam-data-main-writer\n  cancel-in-progress: false$"
)
FRESH_MAIN_CHECKOUT = re.compile(
    r"(?m)^      - name: Checkout repository\n"
    r"        uses: actions/checkout@v5\n"
    r"        with:\n"
    r"          ref: main$"
)
NODE24_PYTHON_SETUP = re.compile(
    r"(?m)^      - name: Set up Python\n"
    r"        uses: actions/setup-python@v6\n"
    r"        with:\n"
    r"          python-version: \"3\.12\"$"
)


class WorkflowConcurrencyTests(unittest.TestCase):
    def test_main_writers_share_one_concurrency_group(self):
        for workflow in WRITING_WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, SHARED_WRITER_CONCURRENCY)

    def test_serialized_writers_checkout_fresh_main_after_waiting(self):
        for workflow in WRITING_WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, FRESH_MAIN_CHECKOUT)

    def test_workflows_use_node24_actions(self):
        for workflow in WRITING_WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertRegex(text, NODE24_PYTHON_SETUP)


if __name__ == "__main__":
    unittest.main()
