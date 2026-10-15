# CampSage

**An open-source AI assistant that helps Integral University students with notices, fees, registration, attendance, timetables and academic questions, using citations.**

Built for Hacktoberfest Hack Day 2026 (Education track) at Integral University, Lucknow.

---

## The problem

Students wait days for answers to recurring questions about exams, fees, registration, attendance, timetables, programmes and course material. The answers already exist in university notices and records, but they are scattered and hard to find.

## What CampSage does

- **Personalised by login.** A student signs in with their college ID, and CampSage loads their semester, class and branch.
- **Bring your own key.** Each student connects their own Google AI Studio API key. There is no shared quota and no cost to the project.
- **Grounded answers only.** Answers come from public notices and that student's own permitted record, nothing else.
- **Always cites its source.** Every answer names the document it came from.
- **Honest about gaps.** If the knowledge base doesn't support an answer, CampSage says "I don't know".
- **Useful for common college problems.** CampSage can explain a notice, point out a fee or registration issue, and say what the available record shows.
- **Stays on topic.** Off-topic questions are politely declined.

## How it works

```
Login (college ID + password)
   → CollegeProvider.verify_login()
   → first login: connect Google AI Studio key (validated with a test call)
   → CollegeProvider.get_profile() → semester, class, branch
Student asks a question
   → retrieve: public notices + this student's permitted records
   → one Gemini Live API turn with the student's own key
   → transcript + native Charon audio + source citation, or "I don't know"
```

**Why Gemini Live?** CampSage follows the Belal55 conversation pattern: it opens a short Gemini Live API turn with the student's own AI Studio key, receives the model's output transcription, and plays the returned native audio using the Charon voice. Student records and retrieved notices are supplied explicitly in the turn prompt.

## Privacy and security

| Concern | How CampSage handles it |
|---|---|
| Student API keys | Encrypted at rest; decrypted in memory per request; never logged or sent to the browser |
| College passwords | Verified by the `CollegeProvider`; stored only as salted hashes in the demo provider |
| Data sent to the model | The question, relevant profile fields, and retrieved notice/record context, not the student's name, ID or password |
| Prompt injection | Notices are treated as untrusted text, and instructions inside them are ignored |
| Secrets in the repo | `.env` and key files are git-ignored |

Students should know that their questions go to Google under their own API key.

## Demo mode

Login and student records are served by a **mock `CollegeProvider`** backed by seeded demo students. It exists because we do not have access to the university's real systems. The interface is two methods:

```python
verify_login(student_id, password) -> bool
get_profile(student_id) -> {"semester": ..., "class": ..., "branch": ...}
```

A real deployment would implement these against the college's own login or API (SSO/OAuth), and CampSage would never need to handle raw college passwords.

**Demo accounts:** `demo-a` / `camp-a-2026`, `demo-b` / `camp-b-2026`, and `demo-c` / `camp-c-2026`.

## Tech stack

- Python 3.11+
- FastAPI (backend) and a simple web UI
- Gemini Live API with native audio and output transcription
- lexical retrieval over page-aware notice chunks
- `cryptography` (Fernet) for key encryption
- SQLite for demo student data

## Getting started locally

```bash
git clone https://github.com/<your-username>/CampSage.git
cd CampSage
python -m venv .venv && source .venv/bin/activate
python setup.py
cp .env.example .env     # configure local paths; the Live model is fixed in code
python -m campsage.ingest
uvicorn campsage.app:app --reload
```

Open `http://localhost:8000`, log in with a demo account, and connect your free key from [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey). The browser UI is being built against the API contract in `docs/DEV_B_WORK.md`.

## Project structure

```
CampSage/
├── campsage/
│   ├── app.py            # FastAPI app and routes
│   ├── college.py        # Demo CollegeProvider boundary
│   ├── keystore.py       # Password-derived encrypted API-key storage
│   ├── session.py        # Expiring server-side sessions
│   ├── gemma.py          # Gemini Live adapter with Charon audio
│   ├── ingest.py         # Page-aware notice chunks
│   ├── retrieve.py       # Local lexical retrieval
│   └── answer.py         # Grounded response and citation validation
├── data/
│   ├── notices/          # Clearly marked sample notices for the demo
│   └── demo_students.json
├── .env.example
├── docs/                 # Private ignored plan and handoff notes
└── LICENSE
```
*(Adjust to match the actual repo.)*

## Judging criteria mapping

| Criterion | Where to look |
|---|---|
| Problem scope | Recurring student queries at Integral University |
| Solution | Personalised, cited, grounded Q&A |
| Technical implementation | Retrieval, per-student encrypted keys, `CollegeProvider` abstraction |
| Voice AI | Gemini Live transcription and native Charon audio |
| Completeness | Login → connect key → ask → cited answer |

## Roadmap

- Real `CollegeProvider` against the university's systems
- Hindi and Awadhi support
- Deadline reminders for exams and fees
- Admin tool for uploading new notices

## License

MIT. See [LICENSE](LICENSE).

## Team

Codemancer: 

Syed Mohammad Abbas Baqri
Syed Abbas Mahdi Rizvi
Shayan Khan
Mohd Sarim
