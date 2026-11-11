# Registration backend (Google Sheet)

1. Create a Google Sheet, e.g. "Anthropic IPO hackathon registrations", in the AlphaStreet Drive.
2. Extensions > Apps Script. Replace the code with `register.gs`. Save.
3. Deploy > New deployment > type **Web app**. Execute as **Me**, who has access **Anyone**. Authorize.
4. Copy the URL ending in `/exec` into `ENDPOINT` in `site/index.html`.
5. Share the sheet with Kajal and Adam (view or edit) for the outreach list.

Re-deploying after a code change: Deploy > Manage deployments > edit > New version (the URL stays the same).
