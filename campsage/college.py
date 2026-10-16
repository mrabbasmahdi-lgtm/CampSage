"""College identity boundary with a clearly fictional demo provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .keystore import hash_password, verify_password


class CollegeProvider(Protocol):
    def verify_login(self, student_id: str, password: str) -> bool: ...

    def get_profile(self, student_id: str) -> dict: ...

    def get_student(self, student_id: str) -> dict: ...


class DemoCollegeProvider:
    """Seeded local data; replace this boundary with official SSO/API later."""

    def __init__(self, path: Path):
        self.students = {row["student_id"]: row for row in json.loads(path.read_text())}

    def verify_login(self, student_id: str, password: str) -> bool:
        row = self.students.get(student_id)
        return bool(row and verify_password(password, row["password_hash"]))

    def get_profile(self, student_id: str) -> dict:
        row = self.students[student_id]
        return {
            "name": row.get("name", ""),
            "semester": row["semester"],
            "class": row["class"],
            "branch": row["branch"],
            "attendance": row["attendance"],
            "timetable": row["timetable"],
            "upcoming_exams": row["upcoming_exams"],
            "fees": row["fees"],
            "registration": row["registration"],
        }

    def get_student(self, student_id: str) -> dict:
        return dict(self.students[student_id])


def make_demo_students(path: Path) -> None:
    """Create demo records once, preserving password hashes in the file."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "student_id": "demo-a",
            "name": "Aarav Sharma",
            "password_hash": hash_password("camp-a-2026"),
            "semester": "B.Tech 5th semester",
            "class": "CSE-A",
            "branch": "Computer Science",
            "attendance": {"overall": "82%", "database systems": "78%"},
            "timetable": {"Monday": ["09:00 Data Mining", "11:00 DBMS"]},
            "upcoming_exams": [{"subject": "DBMS", "date": "2026-10-22"}],
            "fees": {"status": "partial", "outstanding": "₹18,500"},
            "registration": {"status": "open", "note": "Elective registration is pending"},
        },
        {
            "student_id": "demo-b",
            "name": "Zoya Khan",
            "password_hash": hash_password("camp-b-2026"),
            "semester": "BCA 3rd semester",
            "class": "BCA-B",
            "branch": "Computer Applications",
            "attendance": {"overall": "91%", "web technology": "94%"},
            "timetable": {"Tuesday": ["10:00 Web Technology", "14:00 Python Lab"]},
            "upcoming_exams": [{"subject": "Web Technology", "date": "2026-10-24"}],
            "fees": {"status": "paid", "outstanding": "₹0"},
            "registration": {"status": "complete", "note": "No pending registration"},
        },
        {
            "student_id": "demo-c",
            "name": "Rohan Verma",
            "password_hash": hash_password("camp-c-2026"),
            "semester": "B.Tech 7th semester",
            "class": "ECE-A",
            "branch": "Electronics and Communication",
            "attendance": {"overall": "74%", "embedded systems": "69%"},
            "timetable": {"Wednesday": ["09:00 Embedded Systems", "13:00 Project Lab"]},
            "upcoming_exams": [{"subject": "Embedded Systems", "date": "2026-10-28"}],
            "fees": {"status": "overdue", "outstanding": "₹7,200"},
            "registration": {"status": "blocked", "note": "Clear the outstanding fee before registration"},
        },
    ]
    path.write_text(json.dumps(rows, indent=2) + "\n")
