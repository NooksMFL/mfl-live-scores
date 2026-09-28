# MFL Live Scores

Small read-only live score service for the Nooks MFL network.

## What it provides

- Dashboard at `/`
- JSON feed at `/api/scores`
- Club lookup at `/api/score/<club>`
- Health check at `/health`
- MFL authentication using the existing `MFL_REFRESH_TOKEN`
- No MFL credentials stored in GitHub

## MFL API

This project uses the same API pattern as the existing Nooks MFL tools:

- API base: `https://api.playmfl.com`
- Refresh: `POST /auth/refresh`
- Match feed: `GET /matches/feed`

## Deploy on Render

1. Create a new **Web Service** in Render.
2. Connect GitHub repository `NooksMFL/mfl-live-scores`.
3. Render should detect `render.yaml`.
4. Add the secret environment variable:
   - `MFL_REFRESH_TOKEN` = your existing MFL refresh token
5. Deploy.
6. Test:
   - `https://YOUR-SERVICE.onrender.com/health`
   - `https://YOUR-SERVICE.onrender.com/api/scores`
   - `https://YOUR-SERVICE.onrender.com/api/score/Halesowen`

## Optional club list

Set `TRACKED_CLUBS` to a comma-separated list to override the built-in Nooks club list.

Example:

`TRACKED_CLUBS=Halesowen,Gorzow,Pickering,Velez`

## Security

Never commit `MFL_REFRESH_TOKEN` to this repository. Store it only as a hosting-platform secret/environment variable.
