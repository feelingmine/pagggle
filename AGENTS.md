# Pagggle development rules

- Product baseline: `docs/product/外贸独立站 AI 内容运营 PRD V1.0.md`. Follow P0 and M1 → M2 → M3; do not silently change the PRD.
- Keep product documents in `docs/product/`, engineering decisions and verification in `docs/development/`, Python application and tests in `backend/`, browser assets in `frontend/`.
- All local settings belong in root `config.json`. Never print, commit, serve, or place its secrets in prompts. Only `config.example.json` is tracked.
- Runtime databases, fetched sources and customer materials belong in ignored `data/`. Every customer record and query must be scoped by project ID.
- Develop and verify one step at a time. Record actual verification and limitations. Commit each verified step separately when Git writes are permitted; never claim a blocked commit succeeded.
- Keep changes minimal. Preserve original documents, confirmed evidence and manual edits. Unknown facts remain unknown.
- This first pilot is local-only. Project scoping is not authentication or multi-tenant authorization.
