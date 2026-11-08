from dataclasses import dataclass, field
from typing import List


@dataclass
class Task:
    name: str
    description: str = ""
    acceptance: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=list)
    forbid_test_changes: bool = True
    test_command: str = "python -m pytest -q"
    hidden_tests_dir: str = ""
    mutation_threshold: float = 0.0
    max_mutants: int = 12
    mutation_enforce: bool = False
    coverage_threshold: float = 0.0
    coverage_source: str = ""
    coverage_enforce: bool = False
    source: dict = field(default_factory=dict)


@dataclass
class TestStats:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    parse_error: str = ""
    cases: List[dict] = field(default_factory=list)

    def to_dict(self):
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "parse_error": self.parse_error,
        }
