import os
import json
import uuid
from datetime import datetime

from openai import OpenAI

try:
    from datetime import UTC
except ImportError:
    import datetime as dt
    UTC = dt.timezone.utc
from typing import Any, Dict, List, Optional, Union
from flask import Flask, render_template, request, redirect, url_for, flash, abort, session, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from dotenv import load_dotenv
from pydantic import BaseModel
# Local helpers
from grader import grade_quiz
from mailer import send_email

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///quizem.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

llmclient = OpenAI()

class AtomicQuestions(BaseModel):
    questions: list[str]



# Database Models
class User(db.Model):
    __tablename__ = 'users'
    username = db.Column(db.String(64), primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def to_dict(self):
        return {
            'role': self.role,
            'password_hash': self.password_hash
        }

course_students = db.Table('course_students',
    db.Column('course_name', db.String(128), db.ForeignKey('courses.name', ondelete='CASCADE'), primary_key=True),
    db.Column('student_username', db.String(64), db.ForeignKey('users.username', ondelete='CASCADE'), primary_key=True)
)

class Course(db.Model):
    __tablename__ = 'courses'
    name = db.Column(db.String(128), primary_key=True)
    teacher_username = db.Column(db.String(64), db.ForeignKey('users.username', ondelete='SET NULL'))
    students = db.relationship('User', secondary=course_students, 
                               backref=db.backref('courses_enrolled', lazy='dynamic'))

class Quiz(db.Model):
    __tablename__ = 'quizzes'
    id = db.Column(db.String(8), primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    teacher_email = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    available_from = db.Column(db.String(10))  # Storing as string to match current logic
    available_until = db.Column(db.String(10)) # Storing as string to match current logic
    questions = db.Column(JSON().with_variant(JSONB, "postgresql"))

class Response(db.Model):
    __tablename__ = 'responses'
    id = db.Column(db.String(36), primary_key=True)
    quiz_id = db.Column(db.String(8), db.ForeignKey('quizzes.id', ondelete='CASCADE'), nullable=False)
    student_name = db.Column(db.String(255))
    student_email = db.Column(db.String(255))
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    answers = db.Column(JSON().with_variant(JSONB, "postgresql"))
    result = db.Column(JSON().with_variant(JSONB, "postgresql"))


DEFAULT_TEACHER_EMAIL = os.environ.get('TEACHER_EMAIL', '')

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except Exception:
    # Fallback: very weak hashing if Werkzeug isn't available (but Flask bundles it)
    import hashlib

    def generate_password_hash(pw: str):
        return hashlib.sha256(pw.encode('utf-8')).hexdigest()

    def check_password_hash(h: str, pw: str):
        return h == hashlib.sha256(pw.encode('utf-8')).hexdigest()


def ensure_default_users():
    # Create default admin, teacher and student if missing
    for username, role in [('admin', 'admin'), ('teacher', 'teacher'), ('student', 'student')]:
        user = User.query.filter_by(username=username).first()
        if not user:
            new_user = User(
                username=username,
                role=role,
                password_hash=generate_password_hash(username)
            )
            db.session.add(new_user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def init_db(app_instance):
    with app_instance.app_context():
        try:
            db.create_all()
            ensure_default_users()
        except Exception as e:
            print(f"Could not connect to database or create tables: {e}")


init_db(app)


def get_user(username: str):
    if not username:
        return None
    # Case-insensitive lookup
    user = User.query.filter(User.username.ilike(username)).first()
    if user:
        return user.to_dict()
    return None


def set_user(username: str, role: str, password: Optional[str] = None):
    if role not in ('admin', 'teacher', 'student'):
        raise ValueError('Invalid role')
    
    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username, role=role)
        db.session.add(user)
    
    user.role = role
    if password is not None:
        user.password_hash = generate_password_hash(password)
    elif not user.password_hash:
        user.password_hash = generate_password_hash(role)
        
    db.session.commit()


def delete_user(username: str):
    if username in ('admin', 'teacher', 'student'):
        raise ValueError('Cannot delete default user')
    user = User.query.filter_by(username=username).first()
    if user:
        db.session.delete(user)
        db.session.commit()


def _load_courses() -> dict:
    courses = Course.query.all()
    result = {}
    for c in courses:
        result[c.name] = {
            'students': [s.username for s in c.students],
            'teacher': c.teacher_username
        }
    return result


def list_users(role: Optional[str] = None) -> dict:
    if role is None:
        users = User.query.all()
    else:
        users = User.query.filter_by(role=role).all()
    return {u.username: u.to_dict() for u in users}


@app.before_request
def load_current_user():
    username = session.get('username')
    if username:
        u = get_user(username)
        g.user = {'username': username, 'role': u['role']} if u else None
    else:
        g.user = None


@app.context_processor
def inject_user():
    return {'current_user': g.get('user')}


def load_quiz(quiz_id: str):
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz:
        return None
    return {
        'id': quiz.id,
        'title': quiz.title,
        'teacher_email': quiz.teacher_email,
        'created_at': quiz.created_at.isoformat() + 'Z' if quiz.created_at else '',
        'available_from': quiz.available_from,
        'available_until': quiz.available_until,
        'questions': quiz.questions
    }


def save_quiz(quiz_dict: dict):
    quiz_id = quiz_dict['id']
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz:
        quiz = Quiz(id=quiz_id)
        db.session.add(quiz)
    
    quiz.title = quiz_dict.get('title')
    quiz.teacher_email = quiz_dict.get('teacher_email')
    if quiz_dict.get('created_at'):
        try:
            # handle ISO format with Z
            dt_str = quiz_dict['created_at'].replace('Z', '')
            quiz.created_at = datetime.fromisoformat(dt_str)
        except Exception:
            pass
    quiz.available_from = quiz_dict.get('available_from')
    quiz.available_until = quiz_dict.get('available_until')
    quiz.questions = quiz_dict.get('questions')
    
    db.session.commit()


def list_quizzes():
    quizzes = Quiz.query.order_by(Quiz.created_at.desc()).all()
    return [{
        'id': q.id,
        'title': q.title,
        'created_at': q.created_at.isoformat() + 'Z' if q.created_at else '',
        'available_from': q.available_from,
        'available_until': q.available_until,
    } for q in quizzes]


def delete_quiz_data(quiz_id: str):
    quiz = db.session.get(Quiz, quiz_id)
    if quiz:
        db.session.delete(quiz)
        db.session.commit()


def save_response(quiz_id: str, response_dict: dict):
    rid = response_dict.get('id') or str(uuid.uuid4())
    resp = Response(
        id=rid,
        quiz_id=quiz_id,
        student_name=response_dict.get('student_name'),
        student_email=response_dict.get('student_email'),
        answers=response_dict.get('answers'),
        result=response_dict.get('result')
    )
    if response_dict.get('submitted_at'):
        try:
            dt_str = response_dict['submitted_at'].replace('Z', '')
            resp.submitted_at = datetime.fromisoformat(dt_str)
        except Exception:
            pass
    db.session.add(resp)
    db.session.commit()


@app.route('/')
def index():
    quizzes = list_quizzes()
    # If not a teacher, filter out quizzes that are not yet available or have expired
    is_teacher = g.user and g.user.get('role') == 'teacher'
    if not is_teacher:
        now_date = datetime.now(UTC).date().isoformat()
        filtered = []
        for q in quizzes:
            start = q.get('available_from')
            end = q.get('available_until')
            if start and now_date < start:
                continue
            if end and now_date > end:
                continue
            filtered.append(q)
        quizzes = filtered
    return render_template('index.html', quizzes=quizzes)


def login_required():
    if not g.user:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('login', next=request.path))


def roles_required(*roles):
    if not g.user:
        return login_required()
    if g.user['role'] not in roles:
        abort(403)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        # Case-insensitive lookup
        user = User.query.filter(User.username.ilike(username)).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Invalid username or password.', 'error')
            return render_template('login.html')
        actual_username = user.username
        session['username'] = actual_username
        
        flash(f'Logged in as {actual_username}.', 'success')
        nxt = request.form.get('next') or request.args.get('next') or url_for('index')
        return redirect(nxt)
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


@app.route('/quiz/delete/<quiz_id>', methods=['POST'])
def delete_quiz(quiz_id):
    rr = roles_required('teacher')
    if rr:
        return rr
    delete_quiz_data(quiz_id)
    flash(f'Quiz {quiz_id} has been deleted.', 'success')
    return redirect(url_for('index'))


def _valid_username(username: str) -> bool:
    if not username:
        return False
    if len(username) > 64:
        return False
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')
    return all(ch in allowed for ch in username)


@app.route('/manage/users', methods=['GET', 'POST'])
def manage_users():
    rr = roles_required('admin', 'teacher')
    if rr:
        return rr
    
    current_role = g.user['role']
    
    if request.method == 'POST':
        action = request.form.get('action')
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password')
        try:
            if action == 'add':
                role = request.form.get('role', 'student')
                if current_role != 'admin' and role != 'student':
                    raise ValueError('Only admins can create non-student users.')
                
                if not _valid_username(username):
                    raise ValueError('Invalid username. Use letters, numbers, dash or underscore (max 64).')
                if User.query.filter_by(username=username).first():
                    raise ValueError('Username already exists.')
                set_user(username, role, password or role)
                flash(f'Added {role} {username}.', 'success')
            elif action == 'delete':
                delete_user(username)
                flash(f'Deleted user {username}.', 'success')
            elif action == 'reset':
                if not _valid_username(username):
                    raise ValueError('Invalid username.')
                user = User.query.filter_by(username=username).first()
                if not user:
                    raise ValueError('User not found.')
                # Keep existing role
                role = user.role
                if current_role != 'admin' and role != 'student':
                    raise ValueError('Teachers can only reset student passwords.')
                set_user(username, role, password or role)
                flash(f'Password reset for {username}.', 'success')
            else:
                flash('Unknown action.', 'error')
        except Exception as e:
            flash(str(e), 'error')

    if current_role == 'admin':
        # Admin sees everyone except themselves (optional)
        users_to_show = list_users()
        # sort by role then username
        sorted_users = sorted(users_to_show.items(), key=lambda x: (x[1].get('role'), x[0]))
        return render_template('manage_users.html', users=sorted_users, is_admin=True)
    else:
        # Teacher only sees students
        students = sorted(list_users('student').keys())
        return render_template('manage_users.html', students=students, is_admin=False)


@app.route('/manage/courses', methods=['GET', 'POST'])
def manage_courses():
    rr = roles_required('admin')
    if rr:
        return rr
    
    courses = _load_courses()
    
    if request.method == 'POST':
        action = request.form.get('action')
        course_name = (request.form.get('course_name') or '').strip()
        
        try:
            if action == 'create':
                if not course_name:
                    raise ValueError('Course name is required.')
                if Course.query.filter_by(name=course_name).first():
                    raise ValueError('Course already exists.')
                
                course = Course(name=course_name)
                db.session.add(course)
                db.session.commit()
                flash(f'Course "{course_name}" created.', 'success')
            
            elif action == 'delete':
                course = Course.query.filter_by(name=course_name).first()
                if course:
                    db.session.delete(course)
                    db.session.commit()
                    flash(f'Course "{course_name}" deleted.', 'success')
                else:
                    raise ValueError('Course not found.')
            
            elif action == 'assign_teacher':
                teacher_username = request.form.get('teacher_username')
                course = Course.query.filter_by(name=course_name).first()
                if not course:
                    raise ValueError('Course not found.')
                
                course.teacher_username = teacher_username
                db.session.commit()
                flash(f'Teacher {teacher_username} assigned to {course_name}.', 'success')

            elif action == 'assign':
                student_username = request.form.get('student_username')
                course = Course.query.filter_by(name=course_name).first()
                if not course:
                    raise ValueError('Course not found.')
                if not student_username:
                    raise ValueError('Student username is required.')
                
                student = User.query.filter_by(username=student_username).first()
                if not student:
                    raise ValueError('Student not found.')

                if student not in course.students:
                    course.students.append(student)
                    db.session.commit()
                    flash(f'Student {student_username} assigned to {course_name}.', 'success')
                else:
                    flash(f'Student {student_username} is already in {course_name}.', 'info')
            
            elif action == 'unassign':
                student_username = request.form.get('student_username')
                course = Course.query.filter_by(name=course_name).first()
                if not course:
                    raise ValueError('Course not found.')
                
                student = User.query.filter_by(username=student_username).first()
                if student and student in course.students:
                    course.students.remove(student)
                    db.session.commit()
                    flash(f'Student {student_username} removed from {course_name}.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            
    all_students = sorted(list_users('student').keys())
    all_teachers = sorted(list_users('teacher').keys())
    return render_template('manage_courses.html', courses=courses, all_students=all_students, all_teachers=all_teachers)



def get_jev_questions(rubric: List[str]) -> List[str]:
    """
    Convert rubric criteria into atomic evaluation questions using OpenAI structured output.
    """
    if not rubric:
        return []

    rubric_text = "\n".join(
        f"{index + 1}. {point}"
        for index, point in enumerate(rubric)
    )

    instructions = """You convert rubric criteria into atomic evaluation questions.

The questions will be used as independent Noul statements in Jev.

Rules:

1. Each question must evaluate exactly ONE proposition.
2. Each question must be answerable independently.
3. Prefer questions that can be answered yes or no.
4. Begin questions with wording such as:
   - "Does the response..."
   - "Is..."
   - "Are..."
5. Do not combine criteria using "and" or "or".
6. If one rubric item contains multiple requirements, split it into multiple questions.
7. Preserve the meaning of the original rubric.
8. Do not add requirements that are not present in the rubric.
9. Do not omit any requirement.
10. Return only the atomic questions represented by the requested structured output.
11. Make sure the questions are clear, concise, and unambiguous.
12. Make sure the questions can not be subject to interpretation or ambiguity.
"""

    user_prompt = f"Convert the following rubric into isolated atomic questions:\n\n{rubric_text}"

    try:
        model = os.getenv("OPENAI_MODEL", "gpt-6.1-sol")
        response = llmclient.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ],
            response_format=AtomicQuestions,
        )
        result = response.choices[0].message.parsed
        if result and result.questions:
            return result.questions
    except Exception as e:
        print(f"Error calling OpenAI in get_jev_questions: {e}")
        return [r for r in rubric if r.strip()]

    return [r for r in rubric if r.strip()]

@app.route('/teacher/create', methods=['GET', 'POST'])
def teacher_create():
    # Require teacher role
    rr = roles_required('teacher')
    if rr:
        return rr
    if request.method == 'GET':
        # Step 1: Ask for title, teacher email, number of questions
        return render_template('create_quiz.html', default_teacher_email=DEFAULT_TEACHER_EMAIL)

    # POST
    # already authorized above

    # Wizard step detection
    step = request.form.get('wizard_step')

    # Backward compatibility: if legacy JSON is posted, handle it
    if not step and request.form.get('questions_json'):
        title = request.form.get('title', '').strip()
        teacher_email = request.form.get('teacher_email', '').strip() or DEFAULT_TEACHER_EMAIL
        raw = request.form.get('questions_json', '').strip()

        if not title:
            flash('Title is required.', 'error')
            return redirect(url_for('teacher_create'))
        if not raw:
            flash('Questions JSON is required.', 'error')
            return redirect(url_for('teacher_create'))

        try:
            questions = json.loads(raw)
            # Validate minimal structure
            assert isinstance(questions, list) and len(questions) > 0
            for q in questions:
                assert 'text' in q and isinstance(q['text'], str) and q['text'].strip()
                assert 'options' in q and isinstance(q['options'], list) and len(q['options']) >= 2
                assert 'correct_index' in q and isinstance(q['correct_index'], int)
                assert 0 <= q['correct_index'] < len(q['options'])
        except Exception as e:
            flash(f'Invalid questions JSON: {e}', 'error')
            return redirect(url_for('teacher_create'))

        quiz_id = str(uuid.uuid4())[:8]
        quiz = {
            'id': quiz_id,
            'title': title,
            'teacher_email': teacher_email,
            'created_at': datetime.now(UTC).replace(tzinfo=None).isoformat() + 'Z',
            'questions': questions,
        }
        save_quiz(quiz)
        flash('Quiz created successfully.', 'success')
        return render_template('quiz_created.html', quiz=quiz)

    # Wizard step 1 -> render step 2 with question inputs
    if step == '1':
        title = request.form.get('title', '').strip()
        teacher_email = request.form.get('teacher_email', '').strip() or DEFAULT_TEACHER_EMAIL
        num_str = request.form.get('num_questions', '').strip()
        start_date = request.form.get('start_date', '').strip()
        end_date = request.form.get('end_date', '').strip()
        try:
            num_questions = int(num_str)
        except ValueError:
            num_questions = 0

        if not title:
            flash('Title is required.', 'error')
            return redirect(url_for('teacher_create'))
        if num_questions <= 0 or num_questions > 50:
            flash('Please enter a valid number of questions (1-50).', 'error')
            return redirect(url_for('teacher_create'))

        idx_list = list(range(num_questions))
        return render_template(
            'create_quiz_step2.html',
            title=title,
            teacher_email=teacher_email,
            num_questions=num_questions,
            idx_list=idx_list,
            start_date=start_date,
            end_date=end_date,
        )

    # Wizard step 2 -> build questions and create quiz
    if step == '2':
        title = request.form.get('title', '').strip()
        teacher_email = request.form.get('teacher_email', '').strip() or DEFAULT_TEACHER_EMAIL
        num_str = request.form.get('num_questions', '').strip()
        start_date = request.form.get('start_date', '').strip()
        end_date = request.form.get('end_date', '').strip()
        try:
            num_questions = int(num_str)
        except ValueError:
            num_questions = 0

        if not title or num_questions <= 0:
            flash('Invalid data. Please start over.', 'error')
            return redirect(url_for('teacher_create'))

        questions = []
        for i in range(num_questions):
            qtype = (request.form.get(f'q_type_{i}') or 'mc').strip().lower()
            text = (request.form.get(f'q_text_{i}') or '').strip()
            if not text:
                flash('Each question must have text.', 'error')
                return redirect(url_for('teacher_create'))

            if qtype == 'essay':
                reqs_raw = (request.form.get(f'q_requirements_{i}') or '').strip()

                max_pts_raw = (request.form.get(f'q_max_points_{i}') or '').strip()
                # Requirements: separated by two \n characters
                requirements = [r.strip() for r in reqs_raw.replace('\r', '').split('\n\n') if r.strip()]


                print(requirements) ### debug print to check requirements


                if not requirements:
                    flash('Essay questions must include at least one requirement.', 'error')
                    return redirect(url_for('teacher_create'))
                atomic_questions = get_jev_questions(requirements)
                try:
                    max_points = int(max_pts_raw) if max_pts_raw else 10
                except ValueError:
                    max_points = 10
                if max_points <= 0:
                    max_points = 10
                questions.append({
                    'type': 'essay',
                    'text': text,
                    'requirements': atomic_questions,
                    'max_points': max_points,
                })
            else:
                # Multiple choice
                options_raw = (request.form.get(f'q_options_{i}') or '').strip()
                correct_raw = (request.form.get(f'q_correct_{i}') or '').strip()
                points_raw = (request.form.get(f'q_points_{i}') or '').strip()

                if not options_raw:
                    flash('Multiple choice questions must include options.', 'error')
                    return redirect(url_for('teacher_create'))

                # Parse options: split by newlines, commas, or semicolons
                temp = options_raw.replace('\r', '\n')
                temp = temp.replace(';', '\n').replace(',', '\n')
                opts = [o.strip() for o in temp.split('\n') if o.strip()]
                if len(opts) < 2:
                    flash('Each multiple choice question must have at least two options.', 'error')
                    return redirect(url_for('teacher_create'))

                try:
                    correct_num = int(correct_raw)
                except ValueError:
                    flash('Correct option number must be a number for each multiple choice question.', 'error')
                    return redirect(url_for('teacher_create'))

                if correct_num < 1 or correct_num > len(opts):
                    flash('Correct option number must be between 1 and the number of options.', 'error')
                    return redirect(url_for('teacher_create'))

                try:
                    points = int(points_raw) if points_raw else 1
                except ValueError:
                    points = 1
                if points <= 0:
                    points = 1

                questions.append({
                    'type': 'mc',
                    'text': text,
                    'options': opts,
                    'correct_index': correct_num - 1,
                    'points': points,
                })

        quiz_id = str(uuid.uuid4())[:8]
        quiz = {
            'id': quiz_id,
            'title': title,
            'teacher_email': teacher_email,
            'created_at': datetime.now(UTC).replace(tzinfo=None).isoformat() + 'Z',
            'available_from': start_date if start_date else None,
            'available_until': end_date if end_date else None,
            'questions': questions,
        }
        save_quiz(quiz)
        flash('Quiz created successfully.', 'success')
        return render_template('quiz_created.html', quiz=quiz)

    # If we fall through, redirect back to start
    flash('Invalid request.', 'error')
    return redirect(url_for('teacher_create'))


@app.route('/quiz/<quiz_id>', methods=['GET'])
def take_quiz(quiz_id):
    quiz = load_quiz(quiz_id)
    if not quiz:
        abort(404)

    # Check date availability
    now_date = datetime.now(UTC).date().isoformat()
    if quiz.get('available_from') and now_date < quiz['available_from']:
        flash(f"This quiz is not available until {quiz['available_from']}.", 'error')
        return redirect(url_for('index'))
    if quiz.get('available_until') and now_date > quiz['available_until']:
        flash(f"This quiz expired on {quiz['available_until']}.", 'error')
        return redirect(url_for('index'))

    return render_template('take_quiz.html', quiz=quiz)


@app.route('/quiz/<quiz_id>/submit', methods=['POST'])
def submit_quiz(quiz_id):
    quiz = load_quiz(quiz_id)
    if not quiz:
        abort(404)

    student_name = request.form.get('student_name', '').strip()
    student_email = request.form.get('student_email', '').strip()

    answers = []
    for idx, q in enumerate(quiz['questions']):
        qtype = (q.get('type') or 'mc').lower()
        a = request.form.get(f'q_{idx}')
        if qtype == 'essay':
            answers.append((a or '').strip())
        else:
            try:
                answers.append(int(a) if a is not None else None)
            except ValueError:
                answers.append(None)

    result = grade_quiz(quiz, answers)

    response = {
        'id': str(uuid.uuid4()),
        'quiz_id': quiz_id,
        'student_name': student_name,
        'student_email': student_email,
        'submitted_at': datetime.now(UTC).replace(tzinfo=None).isoformat() + 'Z',
        'answers': answers,
        'result': result,
    }
    save_response(quiz_id, response)

    # Email teacher
    teacher_email = quiz.get('teacher_email') or DEFAULT_TEACHER_EMAIL
    if teacher_email:
        subject = f"Quiz Submission: {quiz.get('title')} - {student_name or 'Anonymous'} ({result['score']}/{result['total']})"
        lines = [
            f"Quiz: {quiz.get('title')} ({quiz_id})",
            f"Student: {student_name or 'Anonymous'} <{student_email or 'n/a'}>",
            f"Submitted: {response['submitted_at']}",
            f"Score: {result['score']} / {result['total']} ({result['percent']}%)",
            "",
            "Breakdown:",
        ]
        for i, (q, pq) in enumerate(zip(quiz['questions'], result.get('per_question', []))):
            qtype = (q.get('type') or 'mc').lower()
            lines.append(f"Q{i+1}. [{qtype.upper()}] {q.get('text')}")
            lines.append(f" - Awarded: {pq.get('awarded')}/{pq.get('max_points')}")
            if qtype == 'mc':
                ai = pq.get('details', {}).get('selected_index')
                ci = pq.get('details', {}).get('correct_index')
                opts = q.get('options', [])
                selected_txt = opts[ai] if isinstance(ai, int) and 0 <= ai < len(opts) else 'No answer'
                correct_txt = opts[ci] if isinstance(ci, int) and 0 <= ci < len(opts) else 'N/A'
                lines.append(f" - Selected: {selected_txt}")
                lines.append(f" - Correct: {correct_txt}")
            else:
                eg = pq.get('details', {}).get('essaygrader', {})
                feedback = eg.get('feedback') or []
                essay_text = pq.get('details', {}).get('essay') or eg.get('essay') or ''
                if essay_text:
                    lines.append(f" - Student Essay:\n{essay_text}")
                if feedback:
                    lines.append(" - Essay Evaluation Feedback:")
                    for fb in feedback:
                        prob = fb.get('probability', fb.get('score'))
                        if prob is not None:
                            lines.append(f"    • {fb.get('question')}: {fb.get('answer')} (Probability: {prob:.2f})")
                        else:
                            lines.append(f"    • {fb.get('question')}: {fb.get('answer')}")
                elif eg.get('reasons'):
                    for r in eg.get('reasons', []):
                        lines.append(f"    • {r}")
            lines.append("")
        body = "\n".join(lines)
        send_email(teacher_email, subject, body)

    return render_template('submission_success.html', quiz=quiz, result=result, student_name=student_name)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
