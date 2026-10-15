# CampSage demo data

This directory contains fictional fixture data for local development and judging. It is not
connected to Integral University's real systems.

## Files

- `demo_students.json` — three student records with different academic, attendance, fee, exam,
  timetable, and registration states.
- `notices/attendance_and_course_material.txt` — attendance eligibility and course-material notice.
- `notices/examination_datesheet.txt` — DBMS, Web Technology, and Embedded Systems dates.
- `notices/fee_payment_notice.txt` — sample semester-fee deadline and payment guidance.
- `notices/registration_and_programmes_notice.txt` — elective-registration window and orientation date.
- `notice_index.json` — generated lexical index of the notice files. Rebuild it with:

  ```bash
  python -m campsage.ingest
  ```

`campsage.sqlite3` may appear after local use. It stores encrypted demo API-key material and is
ignored by Git; it must not be committed.

## Demo credentials

| Student ID | Password | Main state |
|---|---|---|
| `demo-a` | `camp-a-2026` | 82% overall attendance, ₹18,500 outstanding, elective registration pending |
| `demo-b` | `camp-b-2026` | 91% overall attendance, fees paid, registration complete |
| `demo-c` | `camp-c-2026` | 74% overall attendance, ₹7,200 overdue, registration blocked |

Use the questions in the root `README.md` to exercise both public notices and student-specific
records.
