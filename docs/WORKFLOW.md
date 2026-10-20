# System Architecture and Execution Workflow Documentation

This document provides a comprehensive analysis of the system architecture, application lifecycle, execution workflows, and detailed function call sequences for the **QuizemJev** platform. It details the exact order in which functions are invoked and the specific parameters passed between components.

---

## 1. High-Level Architecture Overview

The system is organized into modular subsystems:
- **Web Application & Persistence Layer (`app.py`)**: Flask-based web application managing routing, session authentication, role-based access control, relational persistence (via Flask-SQLAlchemy / PostgreSQL / SQLite), and template rendering.
- **Grading Coordinator (`grader.py`)**: Centralized grading engine aggregating scores across multiple choice and free-text essay questions.
- **AI & Heuristic Essay Evaluation Subsystem (`essaygrader.py`)**: Multi-tiered essay evaluation engine supporting local LLM inference (Ollama with semantic embeddings), OpenAI API integration, and deterministic heuristic fallback grading.
- **Email Notification Subsystem (`mailer.py`)**: SMTP client with console logging fallback for notifying instructors of quiz submissions.
- **Command-Line Test Harness (`test.py`)**: Standalone CLI tool for direct testing and evaluation of the essay grading pipeline.

```
                    ┌───────────────────────────────┐
                    │          Client / Web         │
                    └───────────────┬───────────────┘
                                    │ HTTP Requests
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                                 app.py                                    │
│  - Request Lifecycle & Auth Hooks: load_current_user, inject_user         │
│  - User & Course Management: set_user, delete_user, manage_courses        │
│  - Quiz Authoring & Navigation: teacher_create, list_quizzes, take_quiz   │
│  - Submission Orchestrator: submit_quiz ─────────────────────────────┐    │
└─────────────────────────────────┬─��──────────────────────────────────┼────┘
                                  │                                    │
                        load_quiz │ save_response                      │
                                  ▼                                    │
                    ┌───────────────────────────┐                      │
                    │   Database (SQLAlchemy)   │                      │
                    │   User, Course, Quiz,     │                      │
                    │   Response                │                      │
                    └───────────────────────────┘                      │
                                                                       │
                                  ┌────────────────────────────────────┘
                                  │ (quiz, answers)
                                  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                                grader.py                                  │
│  - grade_quiz(quiz, answers)                                              │
│  - _question_type(q), _points_for_mc(q), _points_for_essay(q)             │
└─────────────────┬─────────────────────────────────────────────────────────┘
                  │
                  │ (essay_text, requirements, max_points=100)
                  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              essaygrader.py                               │
│  - grade_essay()                                                          │
│     ├── Path A: _grade_with_openai() [if OPENAI_API_KEY]                  │
│     ├── Path B: Ollama Local LLM Workflow                                 │
│     │    ├── _compute_semantic_score() -> _ollama_embedding()             │
│     │    │                                -> _cosine_similarity()         │
│     │    ├── _build_prompt()                                              │
│     │    ├── _ollama_generate()                                           │
│     │    └── _parse_llm_json() -> _extract_first_json_object()            │
│     └── Path C (or Error Fallback): Deterministic Heuristic Evaluation    │
│          ├── _heuristic_coverage() -> _simple_requirement_match()         │
│          └── _coverage_to_grade()                                         │
│     └── Common: _synthesize_deductions() & GradeResult.to_dict()          │
└─────────────────────────────────┬─────────────────────────────────────────┘
                                  │
                                  │ (returns structured result)
                                  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                                 mailer.py                                 │
│  - send_email(to_email, subject, body)                                    │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. End-to-End Execution Workflows

### 2.1 Application Bootstrap & Database Initialization

When `app.py` is loaded:
1. `init_db(app)` is executed at top-level module import.
   - Pushes Flask application context: `app.app_context()`.
   - Invokes `db.create_all()` to generate tables for `User`, `Course`, `course_students`, `Quiz`, and `Response`.
   - Calls `ensure_default_users()`.
2. `ensure_default_users()` checks for default accounts (`admin`, `teacher`, `student`).
   - For missing users, invokes `generate_password_hash(username)` and commits default user entities to the database.

---

### 2.2 Per-Request Authentication & Context Lifecycle

Every HTTP request passing through Flask triggers:
1. `@app.before_request` -> `load_current_user()`:
   - Reads `session.get('username')`.
   - If present, calls `get_user(username)`.
   - Sets `g.user = {'username': username, 'role': user['role']}` if valid, else `g.user = None`.
2. `@app.context_processor` -> `inject_user()`:
   - Returns `{'current_user': g.get('user')}` into Jinja2 template contexts.
3. Protected Route Verification:
   - Endpoints requiring authorization call `roles_required(*roles)`.
   - If `g.user` is absent, calls `login_required()`, redirecting to `url_for('login', next=request.path)`.
   - If `g.user['role']` is not in allowed `roles`, aborts with HTTP 403 Forbidden.

---

### 2.3 User Authentication Workflow (`/login` & `/logout`)

#### Login (`POST /login`):
1. `login()` extracts `username` and `password` from `request.form`.
2. Queries user case-insensitively via `User.query.filter(User.username.ilike(username)).first()`.
3. Calls `check_password_hash(user.password_hash, password)`.
4. On success: stores `user.username` in `session['username']` and redirects to `next` or `index()`.
5. On failure: flashes error and re-renders `login.html`.

#### Logout (`GET /logout`):
1. `logout()` removes `'username'` from `session`.
2. Flashes success message and redirects to `url_for('index')`.

---

### 2.4 User & Course Management Workflows

#### User Management (`/manage/users`):
1. `manage_users()` verifies permissions via `roles_required('admin', 'teacher')`.
2. If `POST`:
   - `action == 'add'`:
     - Checks permissions (`admin` or `teacher` creating `student`).
     - Validates username with `_valid_username(username)`.
     - Calls `set_user(username, role, password or role)`.
   - `action == 'delete'`:
     - Calls `delete_user(username)` (prevents deleting default users).
   - `action == 'reset'`:
     - Validates username with `_valid_username(username)`.
     - Calls `set_user(username, role, password or role)`.
3. If `GET` / rendering:
   - Admin views all users via `list_users()`.
   - Teacher views student list via `list_users('student')`.

#### Course Management (`/manage/courses`):
1. `manage_courses()` verifies admin role via `roles_required('admin')`.
2. If `POST`:
   - `action == 'create'`: Adds new `Course(name=course_name)`.
   - `action == 'delete'`: Deletes `Course` entity.
   - `action == 'assign_teacher'`: Sets `course.teacher_username = teacher_username`.
   - `action == 'assign'`: Appends `User` (student) to `course.students`.
   - `action == 'unassign'`: Removes `User` from `course.students`.
3. If `GET` / rendering:
   - Calls `_load_courses()` to compile course roster mappings.
   - Calls `list_users('student')` and `list_users('teacher')` for dropdown population.

---

### 2.5 Quiz Creation Workflow (`/teacher/create`)

1. `teacher_create()` verifies teacher role via `roles_required('teacher')`.
2. **GET Request**:
   - Renders `create_quiz.html` with `DEFAULT_TEACHER_EMAIL`.
3. **POST Request - Step 1 (`wizard_step == '1'`):**
   - Validates `title` and `num_questions` (1 to 50).
   - Renders `create_quiz_step2.html` passing `idx_list = list(range(num_questions))`, `start_date`, `end_date`, `title`, and `teacher_email`.
4. **POST Request - Step 2 (`wizard_step == '2'`):**
   - Parses form data for each index `i` in `range(num_questions)`:
     - If `q_type_{i} == 'essay'`: extracts `q_text_{i}`, `q_requirements_{i}` (split by `\n\n`), and `q_max_points_{i}`.
     - If `q_type_{i} == 'mc'`: extracts `q_text_{i}`, `q_options_{i}` (split by delimiters), `q_correct_{i}` (converted to 0-based index `correct_index`), and `q_points_{i}`.
   - Generates UUID `quiz_id`.
   - Constructs quiz dictionary and calls `save_quiz(quiz)`.
   - Renders `quiz_created.html`.
5. **POST Request - Legacy JSON (`questions_json` present, no `wizard_step`):**
   - Deserializes JSON, validates structure, calls `save_quiz(quiz)`, and renders `quiz_created.html`.

---

### 2.6 Quiz Taking & Submission Workflow (`/quiz/<quiz_id>` & `/quiz/<quiz_id>/submit`)

```
User (Browser)               app.py                      grader.py                 essaygrader.py             mailer.py
     │                         │                             │                           │                       │
     │── POST /quiz/id/submit ─>│                             │                           │                       │
     │                         │── load_quiz(quiz_id)        │                           │                       │
     │                         │   (parses form answers)     │                           │                       │
     │                         │                             │                           │                       │
     │                         │── grade_quiz(quiz, answers)─>│                           │                       │
     │                         │                             │── _question_type(q)       │                       │
     │                         │                             │── _points_for_mc(q)       │                       │
     │                         │                             │── _points_for_essay(q)    │                       │
     │                         │                             │                           │                       │
     │                         │                             │── grade_essay(text, reqs)─>                       │
     │                         │                             │   (evaluates via LLM/heur)│                       │
     │                         │                             │<── returns dict ──────────│                       │
     │                         │<── returns graded dict ─────│                                                   │
     │                         │                                                                                 │
     │                         │── save_response(quiz_id, resp)                                                  │
     │                         │                                                                                 │
     │                         │── send_email(teacher_email, subject, body) ────────────────────────────────────>│
     │                         │                                                         (SMTP or log fallback)  │
     │<── renders success ─────│                                                                                 │
```

1. **Viewing Quiz (`GET /quiz/<quiz_id>`):**
   - Calls `load_quiz(quiz_id)`. If not found, triggers `abort(404)`.
   - Validates availability dates against current UTC date (`available_from`, `available_until`).
   - Renders `take_quiz.html`.
2. **Submitting Quiz (`POST /quiz/<quiz_id>/submit`):**
   - Calls `load_quiz(quiz_id)`.
   - Extracts `student_name`, `student_email`, and compiles `answers` list aligned with quiz question indices.
   - Calls `grade_quiz(quiz, answers)` in `grader.py`.
   - Constructs response record with generated UUID and invokes `save_response(quiz_id, response)`.
   - Formats grade breakdown email and calls `send_email(teacher_email, subject, body)` in `mailer.py`.
   - Renders `submission_success.html` with results.

---

### 2.7 Quiz Deletion Workflow (`POST /quiz/delete/<quiz_id>`)

1. `delete_quiz(quiz_id)` verifies teacher role via `roles_required('teacher')`.
2. Calls `delete_quiz_data(quiz_id)` which deletes the `Quiz` record (cascading to dependent `Response` entities).
3. Redirects to `url_for('index')`.

---

### 2.8 CLI Test Execution Workflow (`test.py`)

When running `python test.py [options]`:
1. `main(sys.argv[1:])` is invoked.
2. Calls `parse_args(argv)` to parse command line flags.
3. Calls `collect_inputs(ns)`:
   - If files provided (`--essay-file`, `--requirements-file`), reads them via `_read_text_file(path)`.
   - If raw strings provided (`--essay`, `-r`), parses them.
   - If no arguments, falls back to `_default_sample()`.
4. Calls `grade_essay(essay, requirements, model=..., base_url=..., temperature=..., timeout=...)`.
5. Prints JSON-serialized grading output to standard output.

---

## 3. Detailed Function Reference and Parameter Specifications

### 3.1 `app.py`

#### `generate_password_hash(pw: str) -> str`
- **Caller**: `ensure_default_users()`, `set_user()`.
- **Arguments**:
  - `pw` (`str`): Plaintext password string.
- **Returns**: Hashed password string (via Werkzeug `generate_password_hash` or SHA-256 fallback).

#### `check_password_hash(h: str, pw: str) -> bool`
- **Caller**: `login()`.
- **Arguments**:
  - `h` (`str`): Stored password hash from database.
  - `pw` (`str`): Candidate plaintext password submitted by user.
- **Returns**: `True` if password matches hash, `False` otherwise.

#### `ensure_default_users() -> None`
- **Caller**: `init_db()`.
- **Arguments**: None.
- **Operation**: Queries `User` table for `admin`, `teacher`, and `student`. Adds any missing entities with their username as default password hash.

#### `init_db(app_instance: Flask) -> None`
- **Caller**: Module level in `app.py` (`init_db(app)`).
- **Arguments**:
  - `app_instance` (`Flask`): The Flask application instance.
- **Operation**: Creates application context, invokes `db.create_all()`, and calls `ensure_default_users()`.

#### `get_user(username: str) -> Optional[dict]`
- **Caller**: `load_current_user()`.
- **Arguments**:
  - `username` (`str`): Username string to query.
- **Returns**: Dictionary `{'role': str, 'password_hash': str}` or `None`.

#### `set_user(username: str, role: str, password: Optional[str] = None) -> None`
- **Caller**: `manage_users()`.
- **Arguments**:
  - `username` (`str`): Target username.
  - `role` (`str`): Role string (`'admin'`, `'teacher'`, or `'student'`).
  - `password` (`Optional[str]`): Optional new password. Defaults to role name if unset.
- **Operation**: Upserts `User` record into database.

#### `delete_user(username: str) -> None`
- **Caller**: `manage_users()`.
- **Arguments**:
  - `username` (`str`): Username to delete.
- **Operation**: Deletes `User` record if not one of the default usernames (`admin`, `teacher`, `student`).

#### `_load_courses() -> dict`
- **Caller**: `manage_courses()`.
- **Arguments**: None.
- **Returns**: Dictionary mapping course names to student usernames and teacher username:
  ```python
  {
      course_name: {
          'students': [student_username_1, ...],
          'teacher': teacher_username
      }
  }
  ```

#### `list_users(role: Optional[str] = None) -> dict`
- **Caller**: `manage_users()`, `manage_courses()`.
- **Arguments**:
  - `role` (`Optional[str]`): Optional filter for role type (`'student'`, `'teacher'`, `'admin'`).
- **Returns**: Dictionary mapping username to user dictionary: `{username: {'role': str, 'password_hash': str}}`.

#### `load_current_user() -> None`
- **Caller**: Flask before-request lifecycle hook (`@app.before_request`).
- **Arguments**: None.
- **Operation**: Checks `session['username']`, calls `get_user()`, and populates `g.user`.

#### `inject_user() -> dict`
- **Caller**: Flask template context processor (`@app.context_processor`).
- **Arguments**: None.
- **Returns**: `{'current_user': g.get('user')}`.

#### `load_quiz(quiz_id: str) -> Optional[dict]`
- **Caller**: `take_quiz()`, `submit_quiz()`.
- **Arguments**:
  - `quiz_id` (`str`): 8-character quiz identifier.
- **Returns**: Quiz dictionary containing `id`, `title`, `teacher_email`, `created_at`, `available_from`, `available_until`, and `questions` list; or `None`.

#### `save_quiz(quiz_dict: dict) -> None`
- **Caller**: `teacher_create()`.
- **Arguments**:
  - `quiz_dict` (`dict`): Dictionary containing quiz metadata and `questions` list.
- **Operation**: Upserts `Quiz` model in database and commits session.

#### `list_quizzes() -> list[dict]`
- **Caller**: `index()`.
- **Arguments**: None.
- **Returns**: List of quiz summaries ordered by creation date descending.

#### `delete_quiz_data(quiz_id: str) -> None`
- **Caller**: `delete_quiz()`.
- **Arguments**:
  - `quiz_id` (`str`): ID of quiz to delete.
- **Operation**: Queries `Quiz` by ID, deletes it, and commits transaction.

#### `save_response(quiz_id: str, response_dict: dict) -> None`
- **Caller**: `submit_quiz()`.
- **Arguments**:
  - `quiz_id` (`str`): Associated quiz identifier.
  - `response_dict` (`dict`): Dictionary containing `id`, `student_name`, `student_email`, `submitted_at`, `answers`, and `result`.
- **Operation**: Creates and persists `Response` entity.

#### `login_required() -> Response`
- **Caller**: `roles_required()`.
- **Arguments**: None.
- **Returns**: Redirect response to `/login` with `next` parameter.

#### `roles_required(*roles: str) -> Optional[Response]`
- **Caller**: Route functions (`delete_quiz`, `manage_users`, `manage_courses`, `teacher_create`).
- **Arguments**:
  - `*roles` (`tuple[str]`): Permitted roles (e.g. `'admin'`, `'teacher'`).
- **Returns**: Redirect response (if unauthenticated), aborts 403 (if unauthorized), or `None` (if authorized).

#### `_valid_username(username: str) -> bool`
- **Caller**: `manage_users()`.
- **Arguments**:
  - `username` (`str`): Candidate username.
- **Returns**: `True` if non-empty, length <= 64, and contains only alphanumeric characters, dashes, or underscores.

#### Web Route Functions:
- `index()`: Serves `/`. Calls `list_quizzes()`, applies date filtering for non-teachers, and renders `index.html`.
- `login()`: Serves `/login`. Authenticates credentials against database and sets session.
- `logout()`: Serves `/logout`. Clears session and redirects.
- `delete_quiz(quiz_id)`: Serves `POST /quiz/delete/<quiz_id>`. Calls `delete_quiz_data(quiz_id)`.
- `manage_users()`: Serves `/manage/users`. Handles adding, deleting, and resetting user credentials.
- `manage_courses()`: Serves `/manage/courses`. Handles course creation, deletion, teacher assignment, and student roster management.
- `teacher_create()`: Serves `/teacher/create`. Multi-step wizard creating multiple choice and essay quiz items.
- `take_quiz(quiz_id)`: Serves `/quiz/<quiz_id>`. Validates dates and renders quiz form.
- `submit_quiz(quiz_id)`: Serves `POST /quiz/<quiz_id>/submit`. Coordinates grading via `grade_quiz()`, response persistence, and email notification via `send_email()`.

---

### 3.2 `grader.py`

#### `_question_type(q: Dict[str, Any]) -> str`
- **Caller**: `grade_quiz()`.
- **Arguments**:
  - `q` (`Dict[str, Any]`): Question dictionary.
- **Returns**: `'essay'` if type equals `'essay'`, otherwise `'mc'`.

#### `_points_for_mc(q: Dict[str, Any]) -> int`
- **Caller**: `grade_quiz()`.
- **Arguments**:
  - `q` (`Dict[str, Any]`): Multiple choice question dictionary.
- **Returns**: Integer point value (default: 1).

#### `_points_for_essay(q: Dict[str, Any]) -> int`
- **Caller**: `grade_quiz()`.
- **Arguments**:
  - `q` (`Dict[str, Any]`): Essay question dictionary.
- **Returns**: Integer maximum points (default: 10).

#### `grade_quiz(quiz: Dict[str, Any], answers: List[Any]) -> Dict[str, Any]`
- **Caller**: `submit_quiz()` in `app.py`.
- **Arguments**:
  - `quiz` (`Dict[str, Any]`): Complete quiz object containing `questions` list.
  - `answers` (`List[Any]`): List of submitted answers aligned with question indices (integers for MC, strings for essays).
- **Execution Order**:
  1. Iterates over each question `q` and answer `a` at index `i`.
  2. Calls `_question_type(q)` to branch execution.
  3. For **Essay questions**:
     - Calls `_points_for_essay(q)` to determine `max_pts`.
     - Extracts `requirements` list and student answer `essay_text`.
     - Invokes `grade_essay(essay_text, requirements, max_points=100)` in `essaygrader.py`.
     - Computes scaled score: `round((eg['grade'] / 100.0) * max_pts)`.
     - Appends item breakdown to `per_question` list with details and deductions.
  4. For **Multiple Choice questions**:
     - Calls `_points_for_mc(q)` to determine `max_pts`.
     - Compares answer index `ai` to `q['correct_index']`.
     - Awards full `max_pts` if correct, else 0.
     - Appends item breakdown to `per_question`.
  5. Computes overall score, total available points, and percentage.
- **Returns**: Structured dictionary:
  ```python
  {
      'score': int,
      'total': int,
      'percent': float,
      'per_question': [
          {
              'type': 'mc' | 'essay',
              'awarded': int,
              'max_points': int,
              'correct': bool | None,
              'details': dict
          },
          ...
      ]
  }
  ```

---

### 3.3 `essaygrader.py`

#### `GradeResult.to_dict(self) -> Dict[str, Any]`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**: None (method on dataclass).
- **Returns**: Standard dictionary representation of the grade outcome.

#### `grade_essay(essay: str, requirements: List[str], *, model: Optional[str] = None, base_url: Optional[str] = None, temperature: float = 0.2, timeout: float = 30.0, max_points: int = 100) -> Dict[str, Any]`
- **Caller**: `grade_quiz()` in `grader.py`, `main()` in `test.py`.
- **Arguments**:
  - `essay` (`str`): Student's submitted essay text.
  - `requirements` (`List[str]`): List of rubric requirements.
  - `model` (`Optional[str]`): Model identifier (defaults to `ESSAYGRADER_MODEL` or `"gemma3:12b"`).
  - `base_url` (`Optional[str]`): Ollama API base URL (defaults to `ESSAYGRADER_OLLAMA_BASE_URL` or `"http://localhost:11434"`).
  - `temperature` (`float`): Sampling temperature (default: `0.2`).
  - `timeout` (`float`): Network timeout in seconds (default: `30.0`).
  - `max_points` (`int`): Maximum points scale (default: `100`).
- **Execution Workflow**:
  1. Validates inputs (`essay` non-empty string, `requirements` list of strings).
  2. If `OPENAI_API_KEY` environment variable is present:
     - Delegates directly to `_grade_with_openai()`.
  3. Local Ollama LLM Path:
     - Calls `_compute_semantic_score(...)` to obtain cosine similarity between essay and requirements.
     - Calls `_build_prompt(essay, requirements, similarity_score, max_points)`.
     - Calls `_ollama_generate(...)` to submit prompt to Ollama `/api/generate`.
     - Calls `_parse_llm_json(response_text)`.
     - If response is valid JSON: parses grade, coverage, reasons, and normalizes deductions. If deductions are missing or misaligned, invokes `_synthesize_deductions()`.
     - If response is non-JSON: calls `_heuristic_coverage()`, `_coverage_to_grade()`, and `_synthesize_deductions()`.
  4. Exception / Fallback Path (e.g. Ollama unreachable or connection error):
     - Catches exception and executes deterministic fallback.
     - Calls `_heuristic_coverage(essay, requirements)`.
     - Calls `_coverage_to_grade(coverage, max_points)`.
     - Calls `_synthesize_deductions(grade=..., max_points=..., coverage=...)`.
     - If 404 error, calls `_get_available_ollama_models(base_url)` to provide helpful diagnostics.
  5. Returns `GradeResult.to_dict()`.

#### `_build_prompt(essay: str, requirements: List[str], similarity_score: Optional[float], max_points: int) -> str`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**:
  - `essay` (`str`): Essay body.
  - `requirements` (`List[str]`): List of requirement strings.
  - `similarity_score` (`Optional[float]`): Precomputed cosine similarity score or `None`.
  - `max_points` (`int`): Maximum points.
- **Returns**: Formatted prompt instructing the model to act as an academic evaluator and output strict JSON.

#### `_synthesize_deductions(*, grade: int, max_points: int, coverage: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**:
  - `grade` (`int`): Awarded grade.
  - `max_points` (`int`): Total points possible.
  - `coverage` (`List[Dict[str, Any]]`): List of requirement coverage evaluation dicts.
- **Returns**: Tuple `(deductions_list, total_deductions)` explaining points lost.

#### `_compute_semantic_score(base_url: str, model: str, essay: str, requirements: List[str], timeout: float) -> Optional[float]`
- **Caller**: `grade_essay()`.
- **Arguments**:
  - `base_url` (`str`): Base URL for Ollama.
  - `model` (`str`): Embedding model name.
  - `essay` (`str`): Essay text.
  - `requirements` (`List[str]`): Rubric requirements.
  - `timeout` (`float`): Request timeout in seconds.
- **Execution**:
  1. Combines requirements into a single ideal text string.
  2. Calls `_ollama_embedding(base_url, model, essay, timeout)`.
  3. Calls `_ollama_embedding(base_url, model, ideal_text, timeout)`.
  4. Calls `_cosine_similarity(vec_essay, vec_ideal)`.
- **Returns**: Float similarity score in `[0.0, 1.0]` or `None` on failure.

#### `_get_ssl_context() -> Optional[ssl.SSLContext]`
- **Caller**: `_ollama_embedding()`, `_ollama_generate()`, `_grade_with_openai()`, `_get_available_ollama_models()`.
- **Arguments**: None.
- **Returns**: An `ssl.SSLContext` configured with unverified certificate validation for local loopback connections.

#### `_ollama_embedding(base_url: str, model: str, text: str, timeout: float) -> List[float]`
- **Caller**: `_compute_semantic_score()`.
- **Arguments**:
  - `base_url` (`str`): Ollama URL.
  - `model` (`str`): Model identifier.
  - `text` (`str`): Text string to embed (truncated to 8000 chars).
  - `timeout` (`float`): Timeout in seconds.
- **Execution**:
  1. Attempts POST to `{base_url}/api/embed` (Ollama batch API).
  2. If unavailable, falls back to POST `{base_url}/api/embeddings` (legacy API).
- **Returns**: List of float embedding dimensions.

#### `_cosine_similarity(v1: List[float], v2: List[float]) -> float`
- **Caller**: `_compute_semantic_score()`.
- **Arguments**:
  - `v1` (`List[float]`): First vector.
  - `v2` (`List[float]`): Second vector.
- **Returns**: Dot product divided by magnitudes product (float).

#### `_ollama_generate(*, base_url: str, model: str, prompt: str, temperature: float, timeout: float) -> str`
- **Caller**: `grade_essay()`.
- **Arguments**:
  - `base_url` (`str`): Ollama server address.
  - `model` (`str`): Model name.
  - `prompt` (`str`): Evaluation prompt.
  - `temperature` (`float`): Model sampling temperature.
  - `timeout` (`float`): HTTP timeout.
- **Returns**: Response text generated by the model.

#### `_parse_llm_json(text: str) -> Optional[Dict[str, Any]]`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**:
  - `text` (`str`): Raw LLM response string.
- **Execution**:
  1. Attempts direct `json.loads(text)`.
  2. On failure, calls `_extract_first_json_object(text)` and attempts parsing the extracted substring.
- **Returns**: Parsed dictionary or `None`.

#### `_extract_first_json_object(text: str) -> Optional[str]`
- **Caller**: `_parse_llm_json()`.
- **Arguments**:
  - `text` (`str`): String containing possible JSON substring.
- **Returns**: Substring matching the first balanced `{ ... }` block, or `None`.

#### `_heuristic_coverage(essay: str, requirements: List[str]) -> List[Dict[str, Any]]`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**:
  - `essay` (`str`): Essay text.
  - `requirements` (`List[str]`): List of requirement strings.
- **Execution**:
  - Iterates over requirements and invokes `_simple_requirement_match(essay.lower(), req)`.
- **Returns**: List of coverage dicts: `[{'requirement': str, 'addressed': bool, 'evidence': str}]`.

#### `_simple_requirement_match(essay_lc: str, requirement: str) -> Tuple[bool, str]`
- **Caller**: `_heuristic_coverage()`.
- **Arguments**:
  - `essay_lc` (`str`): Lowercased essay text.
  - `requirement` (`str`): Single requirement string.
- **Returns**: Tuple `(addressed: bool, evidence: str)`.

#### `_coverage_to_grade(coverage: List[Dict[str, Any]], max_points: int) -> int`
- **Caller**: `grade_essay()`, `_grade_with_openai()`.
- **Arguments**:
  - `coverage` (`List[Dict[str, Any]]`): Coverage result list.
  - `max_points` (`int`): Maximum points.
- **Returns**: Integer grade proportional to addressed requirements ratio.

#### `_grade_with_openai(essay: str, requirements: List[str], api_key: str, model: str, temperature: float, timeout: float, max_points: int) -> Dict[str, Any]`
- **Caller**: `grade_essay()`.
- **Arguments**:
  - `essay` (`str`): Student essay.
  - `requirements` (`List[str]`): List of requirements.
  - `api_key` (`str`): OpenAI API secret key.
  - `model` (`str`): OpenAI model identifier (e.g. `'gpt-4o-mini'`).
  - `temperature` (`float`): Temperature parameter.
  - `timeout` (`float`): HTTP timeout in seconds.
  - `max_points` (`int`): Maximum points.
- **Returns**: Dictionary generated from `GradeResult.to_dict()`.

#### `_get_available_ollama_models(base_url: str) -> List[str]`
- **Caller**: `grade_essay()`.
- **Arguments**:
  - `base_url` (`str`): Ollama server URL.
- **Returns**: List of available model name strings queried from `/api/tags`.

---

### 3.4 `mailer.py`

#### `send_email(to_email: str, subject: str, body: str) -> bool`
- **Caller**: `submit_quiz()` in `app.py`.
- **Arguments**:
  - `to_email` (`str`): Recipient email address (e.g., teacher's email).
  - `subject` (`str`): Email subject line string.
  - `body` (`str`): Plaintext email body containing submission metadata and scoring breakdown.
- **Environment Variables Used**:
  - `SMTP_HOST`: Hostname of SMTP server.
  - `SMTP_PORT`: Port number (default: `587`).
  - `SMTP_USER`: SMTP authentication username.
  - `SMTP_PASS`: SMTP authentication password.
  - `SMTP_USE_TLS`: Boolean toggle for STARTTLS (default: `True`).
  - `FROM_EMAIL`: Sender email address (defaults to `SMTP_USER` or `'no-reply@example.com'`).
- **Execution**:
  1. Checks if `SMTP_HOST`, `SMTP_USER`, and `SMTP_PASS` are set. If any are missing, logs email details to stdout and returns `False`.
  2. Constructs `EmailMessage` with headers and body.
  3. Connects via `smtplib.SMTP(host, port, timeout=15)`.
  4. Upgrades connection with `server.starttls()` if TLS enabled.
  5. Authenticates via `server.login(user, password)`.
  6. Sends message via `server.send_message(msg)`.
  7. On SMTP exception, logs error and email body to console fallback and returns `False`.
- **Returns**: `True` if sent successfully over SMTP, `False` otherwise.

---

### 3.5 `test.py`

#### `_read_text_file(path: Union[str, Path]) -> str`
- **Caller**: `collect_inputs()`.
- **Arguments**:
  - `path` (`Union[str, Path]`): File path to read.
- **Returns**: UTF-8 decoded text string from file.

#### `_default_sample() -> tuple[str, list[str]]`
- **Caller**: `collect_inputs()`.
- **Arguments**: None.
- **Returns**: Tuple `(sample_essay_text, sample_requirements_list)` regarding photosynthesis.

#### `parse_args(argv: list[str]) -> argparse.Namespace`
- **Caller**: `main()`.
- **Arguments**:
  - `argv` (`list[str]`): Command-line argument list.
- **Returns**: Parsed `argparse.Namespace` object.

#### `collect_inputs(ns: argparse.Namespace) -> tuple[str, list[str]]`
- **Caller**: `main()`.
- **Arguments**:
  - `ns` (`argparse.Namespace`): Parsed command-line arguments.
- **Returns**: Tuple `(essay_text, requirements_list)`.

#### `main(argv: list[str]) -> int`
- **Caller**: Top-level script entry point (`if __name__ == '__main__': raise SystemExit(main(sys.argv[1:]))`).
- **Arguments**:
  - `argv` (`list[str]`): Command-line arguments.
- **Execution**:
  1. Calls `parse_args(argv)`.
  2. Calls `collect_inputs(ns)`.
  3. Calls `grade_essay(...)` in `essaygrader.py`.
  4. Dumps JSON output to standard output via `json.dumps(result, ensure_ascii=False, indent=2)`.
- **Returns**: Process exit code integer (`0` on success).

---

## 4. Function Call Sequence Summary Table

| Stage / Flow | Calling Function | Target Function | Key Arguments Passed | Output / Destination |
|---|---|---|---|---|
| **App Startup** | `init_db` | `db.create_all` | - | Creates DB tables |
| **App Startup** | `init_db` | `ensure_default_users` | - | Initializes default roles |
| **App Startup** | `ensure_default_users` | `generate_password_hash` | `username` | Password hash |
| **Request Pre-hook**| `@app.before_request` | `load_current_user` | - | Sets `g.user` |
| **Request Pre-hook**| `load_current_user` | `get_user` | `session['username']` | User dict |
| **Context Hook** | `@app.context_processor` | `inject_user` | - | `{'current_user': g.user}` |
| **User Mgmt** | `manage_users` | `_valid_username` | `username` | Validation boolean |
| **User Mgmt** | `manage_users` | `set_user` | `username, role, password` | DB upsert |
| **User Mgmt** | `manage_users` | `delete_user` | `username` | DB delete |
| **Course Mgmt** | `manage_courses` | `_load_courses` | - | Course mapping dict |
| **Quiz Creation**| `teacher_create` | `save_quiz` | `quiz_dict` | DB upsert |
| **Quiz View** | `take_quiz` | `load_quiz` | `quiz_id` | Quiz dictionary |
| **Quiz Submit** | `submit_quiz` | `load_quiz` | `quiz_id` | Quiz dictionary |
| **Quiz Submit** | `submit_quiz` | `grade_quiz` | `quiz, answers` | Scoring breakdown dict |
| **Quiz Submit** | `submit_quiz` | `save_response` | `quiz_id, response_dict`| DB insert |
| **Quiz Submit** | `submit_quiz` | `send_email` | `teacher_email, subject, body` | Notification dispatch |
| **Quiz Deletion**| `delete_quiz` | `delete_quiz_data` | `quiz_id` | DB delete |
| **Grading Core** | `grade_quiz` | `_question_type` | `q` | `'mc'` or `'essay'` |
| **Grading Core** | `grade_quiz` | `_points_for_mc` | `q` | Max MC points |
| **Grading Core** | `grade_quiz` | `_points_for_essay` | `q` | Max essay points |
| **Grading Core** | `grade_quiz` | `grade_essay` | `essay_text, requirements, max_points=100` | Essay grade result dict |
| **Essay Grading**| `grade_essay` | `_compute_semantic_score` | `base_url, model, essay, requirements, timeout` | Cosine similarity float |
| **Essay Grading**| `grade_essay` | `_build_prompt` | `essay, requirements, similarity_score, max_points` | Formatted prompt string |
| **Essay Grading**| `grade_essay` | `_ollama_generate` | `base_url, model, prompt, temperature, timeout` | LLM response string |
| **Essay Grading**| `grade_essay` | `_parse_llm_json` | `response_text` | Parsed JSON dict |
| **Essay Grading**| `_parse_llm_json` | `_extract_first_json_object` | `text` | JSON substring |
| **Essay Grading**| `grade_essay` | `_synthesize_deductions`| `grade, max_points, coverage` | Deductions list & total |
| **Essay Fallback**| `grade_essay` | `_heuristic_coverage` | `essay, requirements` | Coverage list |
| **Essay Fallback**| `_heuristic_coverage` | `_simple_requirement_match` | `essay_lc, requirement` | `(addressed, evidence)` |
| **Essay Fallback**| `grade_essay` | `_coverage_to_grade` | `coverage, max_points` | Integer grade score |
| **Essay Fallback**| `grade_essay` | `_get_available_ollama_models`| `base_url` | Available model names |
| **OpenAI Path** | `grade_essay` | `_grade_with_openai` | `essay, requirements, api_key, model, ...` | OpenAI grade result dict |
| **CLI Harness** | `main` | `parse_args` | `argv` | Parsed CLI namespace |
| **CLI Harness** | `main` | `collect_inputs` | `ns` | `(essay, requirements)` |
| **CLI Harness** | `collect_inputs` | `_read_text_file` | `path` | Text content string |
| **CLI Harness** | `collect_inputs` | `_default_sample` | - | Sample essay & rubric |
| **CLI Harness** | `main` | `grade_essay` | `essay, requirements, ...` | Grade result dict |
