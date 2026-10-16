# CampSage demo data

This directory contains fictional fixture data for local development and judging. It is not
connected to Integral University's real systems.

## Files

- `demo_students.json` — six student records across five programmes. Each record has attendance
  per subject, a weekly timetable, exams (date, time, venue, seat), admit-card status, fee
  breakdown and due dates, registration, courses and faculty, assignments, results and backlogs,
  library, scholarship and mentor. Some records also have hostel, placement, revaluation and
  attendance-condonation details.
- `notices/` — public notices (all marked DEMO):
  - `attendance_and_course_material.txt` — 75% rule, condonation, course material
  - `examination_datesheet.txt` — datesheets for all demo programmes
  - `exam_rules_and_admit_card.txt` — admit card, reporting time, allowed items
  - `revaluation_and_backlog_notice.txt` — revaluation and backlog exam fees and dates
  - `fee_payment_notice.txt` — deadline, late fee, instalments, how to pay
  - `registration_and_programmes_notice.txt` — elective/project registration, orientation, workshops
  - `scholarship_notice.txt` — merit and need-based scholarships
  - `hostel_rules_notice.txt`, `library_rules_notice.txt`
  - `placement_and_training_notice.txt`
  - `holiday_and_academic_calendar.txt`
  - `certificates_and_id_card_notice.txt`
  - `helpdesk_and_grievance_notice.txt`
  - `procedure_*.txt` — nine step-by-step procedures: fee paid but still pending, elective
    registration problem, attendance shortage and condonation, exam form or admit card issue, lost
    ID card, bonafide certificate, scholarship query, hostel or transport fee query, and
    correcting name or details.
  - More `procedure_*.txt` for problems where students lose days going between offices: backlog
    exam form, revaluation and answer-sheet copy, result showing absent or marks missing, missed
    exam or exam clash, lost admit card, wrong attendance record, double payment and refund,
    instalment or late-fee waiver, scholarship not credited, hostel room change and complaint,
    transport pass, transfer/migration/degree certificate and no-dues order, internship NOC and
    project guide, wrong section or timetable clash, and portal login reset.
  - `where_to_go_quick_guide.txt` — which office handles what, with block and room.
  - `staff_cse_faculty_offices.txt`, `staff_ece_faculty_offices.txt`,
    `staff_computer_applications_faculty_offices.txt` — fictional faculty per department (name,
    block, floor, room, extension, office hours).
  - `office_admission.txt`, `office_accounts.txt`, `office_examination.txt`,
    `office_scholarship_hostel_transport.txt` — fictional office contacts and what each handles.
  - `campus_overview.txt` — fictional block names and what is in each block.
- `notice_index.json` — generated lexical index of the notice files. Rebuild it with:

  ```bash
  python -m campsage.ingest
  ```

`campsage.sqlite3` may appear after local use. It stores encrypted demo API-key material and is
ignored by Git; it must not be committed.

## Demo credentials

| Student ID | Password | Name | Programme | Main state |
|---|---|---|---|---|
| `demo-a` | `camp-a-2026` | Arham Siddiqui | B.Tech CSE, 5th sem | 82% attendance, ₹18,500 due, elective registration pending, assignment not submitted, 4th-sem Software Testing result shows absent, attendance correction waiting |
| `demo-b` | `camp-b-2026` | Zainab Fatima | BCA, 3rd sem | 91% attendance, ₹12,000 payment made 15 Oct 2026 (receipt DEMO-RCPT-48213) not yet reflected, registration complete, eligible for merit scholarship |
| `demo-c` | `camp-c-2026` | Rayyan Ansari | B.Tech ECE, 7th sem | 74% attendance, ₹7,200 overdue, registration blocked, admit card on hold, 1 backlog, overdue library book, DSP backlog form not filled (late form until 20 Oct) |
| `demo-d` | `camp-d-2026` | Hamza Qureshi | B.Tech ME, 3rd sem | 96% attendance, CGPA 9.0, active merit scholarship (50% tuition waiver), bus pass expired |
| `demo-e` | `camp-e-2026` | Ayesha Naqvi | MBA, 1st sem | 88% attendance, second fee instalment of ₹37,500 due, hostel allotted, room-change request with no reply for 15 days |
| `demo-f` | `camp-f-2026` | Danish Akhtar | B.Tech CSE, 7th sem | 79% attendance, 1 backlog (Operating Systems), revaluation under review, placement drives registered, backlog form filled |

Use the questions in the root `README.md` to exercise both public notices and student-specific
records.

All names, staff, rooms, extensions and receipt numbers in this folder are fictional sample data
(marked "DEMO") and must be replaced with real details before a real deployment.
