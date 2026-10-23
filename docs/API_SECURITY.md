# API Security Configuration

## CORS Policy
zer0Track intentionally does not configure CORS middleware.
The Streamlit dashboard calls FastAPI from the server side.
Browser cross-origin access is not enabled. CORS is not authentication.
If browser-side requests are added later, use an explicit trusted-origin allowlist.

## API Documentation
FastAPI /docs, /redoc and /openapi.json are disabled by default.

## Scope
This is a synthetic-data demonstration, not a production-ready authentication system.
