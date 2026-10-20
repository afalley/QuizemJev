# QuizemJev

QuizemJev is an AI-powered quiz and essay grading web application built with Flask, SQLAlchemy, OpenAI Structured Outputs, and Jev System 1 via the TypeSafe SDK. It supports role-based access control (Admin, Teacher, Student), automated grading for multiple-choice questions, atomic rubric decomposition, continuous probabilistic essay evaluation, and automated email notifications.

---

## Features

- **Role-Based Access Control**: Separate workflows for `admin` (user & course management), `teacher` (quiz authoring, rubric criteria, roster views), and `student` (quiz taking, immediate scoring).
- **Hybrid Quiz Types**: Supports both traditional Multiple Choice (MC) questions and free-text Essay questions.
- **Rubric Decomposition**: Decomposes complex grading rubrics into atomic Yes/No evaluation questions via OpenAI structured outputs.
- **AI Essay Grading**: Evaluates student essays using Jev System 1 via TypeSafe SDK with probabilistic scoring and itemized feedback.
- **Database Persistence**: Compatible with SQLite for local development and PostgreSQL for production deployments.
- **Submission Notifications**: Automated SMTP email notifications with scoring breakdowns sent to instructors.

---

## Local Development Setup

### 1. Prerequisites
- Python 3.10+ (or Python 3.11 / 3.12 / 3.14)
- Git

### 2. Clone and Setup Environment
```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/QuizemJev.git
cd QuizemJev

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root:
```env
FLASK_SECRET_KEY=dev-secret-key-change-in-production
OPENAI_API_KEY=your-openai-api-key
OPEN_ROUTER_KEY=your-openrouter-key

# Optional: Email notification settings (falls back to console logging if unset)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASS=your-email-password-or-app-password
SMTP_USE_TLS=true
FROM_EMAIL=your-email@example.com
```

### 4. Run the Application
```bash
python app.py
```
Open [http://localhost:8080](http://localhost:8080) in your browser.

Default user accounts (auto-seeded on first run):
- **Admin**: `admin` / `admin`
- **Teacher**: `teacher` / `teacher`
- **Student**: `student` / `student`

---

## Deploying to Heroku

Follow the steps below to deploy QuizemJev to [Heroku](https://www.heroku.com/).

### Deployment Requirements Checklist
Before deploying, ensure the repository contains the following:
1. **`Procfile`**: Defines the web process entrypoint for Gunicorn:
   ```text
   web: gunicorn app:app
   ```
2. **`requirements.txt`**: Lists all required Python packages (including `gunicorn`, `psycopg2-binary`, `Flask`, `Flask-SQLAlchemy`, `openai`, `pydantic`, `typesafe-sdk`).
3. **Database URI Compatibility**: Handled in `app.py` (automatically formats Heroku's `postgres://` connection string to `postgresql://`).

---

### Step-by-Step Heroku Deployment Guide

#### Step 1: Install & Login to Heroku CLI
If you haven't installed the Heroku CLI yet:
- **macOS (Homebrew)**: `brew tap heroku/brew && brew install heroku`
- **Windows / Linux**: Download from [cli-assets.heroku.com](https://devcenter.heroku.com/articles/heroku-cli)

Log in to your Heroku account:
```bash
heroku login
```

#### Step 2: Create a Heroku App
From your project root directory, create a new Heroku application:
```bash
heroku create quizemjev-app
```
*(Replace `quizemjev-app` with your preferred unique application name, or run `heroku create` to generate a random name).*

#### Step 3: Add Heroku Postgres
Provision a PostgreSQL database addon:
```bash
heroku addons:create heroku-postgresql:essential-0
```
This automatically provisions a PostgreSQL database and injects the `DATABASE_URL` config var into your Heroku application.

#### Step 4: Configure Heroku Environment Variables (Config Vars)
Set the required and optional environment variables on Heroku:

```bash
# Required: Secret key for Flask session security
heroku config:set FLASK_SECRET_KEY="$(openssl rand -hex 32)"

# Required: API keys for rubric decomposition and essay grading
heroku config:set OPENAI_API_KEY="your-openai-api-key"
heroku config:set OPEN_ROUTER_KEY="your-openrouter-key"

# Optional: SMTP Email configuration (for quiz submission notification emails)
heroku config:set SMTP_HOST="smtp.gmail.com"
heroku config:set SMTP_PORT="587"
heroku config:set SMTP_USER="your-email@example.com"
heroku config:set SMTP_PASS="your-app-password"
heroku config:set SMTP_USE_TLS="true"
heroku config:set FROM_EMAIL="your-email@example.com"
```

#### Step 5: Deploy Code via Git
Commit your changes and push to Heroku:
```bash
# Add files and commit
git add .
git commit -m "Configure application for Heroku deployment"

# Push to Heroku main branch
git push heroku main
```

#### Step 6: Verify Deployment and Open App
Ensure at least one web dyno is running:
```bash
heroku ps:scale web=1
```

Open your deployed application in your browser:
```bash
heroku open
```

To view live application logs for debugging:
```bash
heroku logs --tail
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Auto-set | `sqlite:///quizem.db` | PostgreSQL database connection URL (managed automatically by Heroku Postgres). |
| `FLASK_SECRET_KEY` | Recommended | `dev-secret-key` | Cryptographic key used by Flask to sign session cookies. |
| `OPENAI_API_KEY` | Yes | - | API key for OpenAI (used for atomic rubric question decomposition). |
| `OPEN_ROUTER_KEY` | Yes | - | API key for OpenRouter / TypeSafe SDK (used for Jev System 1 essay grading). |
| `OPENAI_MODEL` | No | `gpt-6.1-sol` | OpenAI model identifier used for rubric decomposition. |
| `SMTP_HOST` | No | - | Hostname of the outgoing SMTP server. |
| `SMTP_PORT` | No | `587` | Port number for SMTP server. |
| `SMTP_USER` | No | - | Username for SMTP server authentication. |
| `SMTP_PASS` | No | - | Password / App password for SMTP server authentication. |
| `SMTP_USE_TLS` | No | `true` | Whether to enable STARTTLS for SMTP connections (`true` / `false`). |
| `FROM_EMAIL` | No | `SMTP_USER` or `no-reply@example.com` | Sender address displayed on notification emails. |
| `PORT` | Auto-set | `8080` | Port on which the web server listens (managed automatically by Heroku). |

---

## Troubleshooting & Tips

- **Database Initialization**: The database schema and default users (`admin`, `teacher`, `student`) are initialized automatically when the Flask application starts on Heroku.
- **Resetting Passwords**: Log in as `admin` to manage and reset user passwords at `/manage/users`.
- **Viewing Logs**: If you encounter errors, run `heroku logs --tail` to inspect runtime tracebacks.
- **Email Delivery**: If SMTP variables are not configured, submission details will be logged directly to Heroku stdout (viewable with `heroku logs --tail`).
