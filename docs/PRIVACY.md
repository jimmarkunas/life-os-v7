# Privacy rule (HARD RULE - this repository is PUBLIC)

Anything committed here, written in a PR, or printed in a GitHub Actions log is visible to the world and cached forever.

**Never commit, paste, or log:**
- real names, email addresses, phone numbers, home address, employer, salary, resume content;
- real mailbox content: sender addresses, subjects, job titles, company names, job URLs (tracking links carry personal tokens);
- secrets of any kind: passwords, API keys, OAuth client secrets, refresh tokens, SSH keys, Notion tokens, database names/hosts/users;
- Notion page/database IDs, Hostinger hostnames, GitHub Actions variable *values*.

**Where private things live:** GitHub Actions *Secrets* (credentials), the Hostinger database (job data), Notion (published jobs). The repo holds **code and synthetic examples only**.

**Rules for code**
1. Logs emit **counts and status codes only** - never a subject, sender, company, title, or URL.
2. Tests use **synthetic** data (`example.com`, invented names). Never copy a real email into a fixture.
3. Errors never echo HTTP response bodies.
4. `tests/test_privacy.py` runs in CI and fails the build on emails outside an allowlist, secret-shaped strings, and phone numbers. Fix the leak; never weaken the test to pass.

**Rules for the assistant:** never paste secrets or mailbox content into chat, commits, PRs, or issues; describe data by count/category.
