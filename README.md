# zer0Track

An AI agent that checks parcel handoff evidence and recommends whether a delivery is ready to accept or needs review.

**Track:** Secure Logistics & Digital Trust — Handoff Trust Verifier
**Event:** GDEX x ANON x UTAR Agentic AI Cybersecurity Hackathon 2026
**Team:** Raja, Win, Nour, Bhone, Kogu

Full write-up: [`docs/zer0Track.proposal.pdf`](docs/zer0Track.proposal.pdf)
Pitch deck: [`docs/Zer0Track_pitchdeck_slides.pdf`](docs/Zer0Track_pitchdeck_slides.pdf)
Role breakdown + file/function conventions: [`docs/zer0Track_Role_Outlines.pdf`](docs/zer0Track_Role_Outlines.pdf)

## Repo structure

```
zer0track/
├── backend/                   # Bhone — API, checks, policy, audit, data models (schema source of truth)
├── agent/                     # Kogu — LLM investigator, tool allowlist, injection tests
├── data/                      # Raja — synthetic dataset generation
├── evaluation/                # Raja — baseline/rules/LLM comparison, metrics
├── dashboard/                 # Nour — Streamlit dashboard, investigation timeline
└── docs/                      # Win — proposal, pitch deck, role outlines, threat model, agent output contract, demo
```

## Workflow

1. Read `backend/models.py` and `docs/AGENT_OUTPUT_CONTRACT.md` — every file's input/output shapes are defined there.
2. Work on your own branch, named after your area: `feature/backend`, `feature/agent`, `feature/data`, `feature/dashboard`.
3. Open a Pull Request into `main` when a piece is ready — don't push straight to `main`.
4. If you need to change a shape in `backend/models.py`, flag it in the group chat first — everyone else's code depends on it staying stable.

## Setup

```bash
git clone https://github.com/koguuu/zer0track.git
cd zer0track
git checkout -b feature/<your-area>
```
