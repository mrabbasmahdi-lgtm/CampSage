# CampSage

**An open-source AI assistant that answers Integral University students' recurring questions, from public notices and their own academic record, with citations.**

Built for Hacktoberfest Hack Day 2026 (Education track) at Integral University, Lucknow.

---

## The problem

Students wait days for answers to recurring questions about exams, fees, registration and course material. The answers already exist in university notices and records, but they are scattered and hard to find.

## What CampSage does

- **Personalised by login.** A student signs in with their college ID, and CampSage loads their semester, class and branch.
- **Bring your own key.** Each student connects their own Google AI Studio API key. There is no shared quota and no cost to the project.
- **Grounded answers only.** Answers come from public notices and that student's own record, nothing else.
- **Always cites its source.** Every answer names the document it came from.
- **Honest about gaps.** If the knowledge base doesn't support an answer, CampSage says "I don't know".
- **Stays on topic.** Off-topic questions are politely declined.

## How it works

```
Login (college ID + password)
   → CollegeProvider.verify_login()
   → first login: connect Google AI Studio key (validated with a test call)
   → CollegeProvider.get_profile() → semester, class, branch
Student asks a question
   → retrieve: public notices + this student's records
   → Gemma 4 (student's own key) answers from that context only
   → answer + source citation, or "I don't know"
```

**Why Gemma 4?** It is an open-weight model reachable through the Gemini API with the student's own AI Studio key. It can also be swapped for a locally hosted open model via Ollama.

## Privacy and security

| Concern | How CampSage handles it |
|---|---|
| Student API keys | Encrypted at rest; decrypted in memory per request; never logged or sent to the browser |
| College passwords | Verified by the `CollegeProvider`; stored only as salted hashes in the demo provider |
| Data sent to the model | Semester and class only, not name or ID |
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

**Demo accounts:** `<id>` / `<password>` (see `data/demo_students.json`)

## Tech stack

- Python 3.11+
- FastAPI (backend) and a simple web UI
- Gemma 4 via the Gemini API (open-weight model)
- `<retrieval library, e.g. FAISS / ChromaDB>`
- `cryptography` (Fernet) for key encryption
- SQLite for demo student data

## Getting started

```bash
git clone https://github.com/<your-username>/CampSage.git
cd CampSage
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # set ENCRYPTION_SECRET
python -m campsage.ingest   # index the notices in data/notices/
uvicorn campsage.app:app --reload
```

Open `http://localhost:8000`, log in with a demo account, and connect your free key from [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).

## Project structure

```
CampSage/
├── campsage/
│   ├── app.py            # FastAPI app and routes
│   ├── college.py        # CollegeProvider interface + mock implementation
│   ├── keystore.py       # Encrypted per-student API key storage
│   ├── ingest.py         # Chunk and index notices / records
│   ├── retrieve.py       # Retrieval for a given student
│   └── answer.py         # Gemma 4 call, citations, "I don't know" fallback
├── data/
│   ├── notices/          # Public university notices
│   └── demo_students.json
├── .env.example
└── LICENSE
```
*(Adjust to match the actual repo.)*

## Judging criteria mapping

| Criterion | Where to look |
|---|---|
| Problem scope | Recurring student queries at Integral University |
| Solution | Personalised, cited, grounded Q&A |
| Technical implementation | Retrieval, per-student encrypted keys, `CollegeProvider` abstraction |
| Open-source AI | Gemma 4 (open-weight); optional local Ollama |
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
