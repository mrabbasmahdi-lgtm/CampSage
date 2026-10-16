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
- `notices/procedure_*.txt` — nine DEMO step-by-step procedures: fee paid but still pending,
  elective registration problem, attendance shortage and condonation, exam form or admit card
  issue, lost ID card, bonafide certificate, scholarship query, hostel or transport fee query,
  and correcting name or details.
- `notices/staff_cse_faculty_offices.txt`, `staff_ece_faculty_offices.txt`, `staff_computer_applications_faculty_offices.txt` — fictional faculty
  per department (name, block, floor, room, extension, office hours).
- `notices/office_admission.txt`, `office_accounts.txt`, `office_examination.txt`,
  `office_scholarship_hostel_transport.txt` — fictional office contacts and what each office handles.
- `notices/campus_overview.txt` — fictional block names and what is in each block.
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
| `demo-b` | `camp-b-2026` | 91% overall attendance, ₹12,000 payment made 15 Oct 2026 (receipt DEMO-RCPT-48213) not yet reflected, registration complete |
| `demo-c` | `camp-c-2026` | 74% overall attendance, ₹7,200 overdue, registration blocked |

Use the questions in the root `README.md` to exercise both public notices and student-specific
records.

## Demo student names

`demo-a` is Arham Siddiqui, `demo-b` is Zainab Fatima and `demo-c` is Rayyan Ansari. All names,
staff, rooms, extensions and receipt numbers in this folder are fictional sample data (marked
"DEMO") and must be replaced with real details before a real deployment.
