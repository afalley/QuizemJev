from __future__ import annotations

from typing import List, Dict, Any, Optional, Union

# Essay grading helper using Jev
from essaygrader import grade_essay, jev_grader


def _question_type(q: Dict[str, Any]) -> str:
    """
    Determine question type with backwards compatibility.
    Defaults to 'mc' (multiple choice) if not specified.
    """
    t = (q.get('type') or 'mc').strip().lower()
    return 'essay' if t == 'essay' else 'mc'


def _points_for_mc(q: Dict[str, Any]) -> int:
    """Return the max points for an MC question (default 1)."""
    try:
        return int(q.get('points', 1))
    except Exception:
        return 1


def _points_for_essay(q: Dict[str, Any]) -> int:
    """Return the max points for an essay question (default 10)."""
    try:
        return max(1, int(q.get('max_points', q.get('points', 10))))
    except Exception:
        return 10


def grade_quiz(quiz: Dict[str, Any], answers: List[Any]) -> Dict[str, Any]:
    """
    Grade a quiz entirely on the server side.

    Supports two question types:
    - 'mc' (multiple choice): fields: text, options[list], correct_index[int], optional points[int]
    - 'essay': fields: text (prompt), requirements[list[str]], max_points[int] (or points)

    answers is a list aligned with quiz['questions'] containing for each index:
    - for 'mc': selected option index (int) or None
    - for 'essay': free-text answer (str) or ''/None

    Returns a dict with totals based on a max grade of 100, and per-question details:
    {
      score: int | float,         # final quiz score on a max grade of 100
      total: int,                 # 100 (max grade of 100)
      percent: float,             # 0..100
      raw_score: int,             # sum of raw awarded points
      raw_total: int,             # sum of raw max points
      per_question: [
         {
           type: 'mc'|'essay',
           awarded: int,
           max_points: int,
           correct: bool | None,  # None for essay
           details: {...},        # includes essaygrader artifacts when essay
         },
      ]
    }
    """
    questions: List[Dict[str, Any]] = quiz.get('questions', [])

    total_points = 0
    score_points = 0
    per_question: List[Dict[str, Any]] = []

    for i, q in enumerate(questions):
        qtype = _question_type(q)
        a = answers[i] if i < len(answers) else None

        if qtype == 'essay':
            max_pts = _points_for_essay(q)
            requirements = q.get('requirements') or []
            if not isinstance(requirements, list):
                requirements = []
            essay_text = a if isinstance(a, str) else (a or '')

            # Call essaygrader (which uses jev_grader).
            eg = grade_essay(essay_text, requirements, max_points=100)
            # Map 0..100 grade to 0..max_pts
            awarded = round((int(eg.get('grade', 0)) / 100) * max_pts)
            awarded = max(0, min(max_pts, awarded))

            per_question.append({
                'type': 'essay',
                'awarded': awarded,
                'max_points': max_pts,
                'correct': None,
                'details': {
                    'prompt': q.get('text'),
                    'requirements': requirements,
                    'essay': essay_text,
                    'essaygrader': eg,
                },
            })
            score_points += awarded
            total_points += max_pts

        else:  # multiple choice
            max_pts = _points_for_mc(q)
            ci = q.get('correct_index')
            ai = a if isinstance(a, int) else None
            correct = (ai is not None) and (ai == ci)
            awarded = max_pts if correct else 0

            per_question.append({
                'type': 'mc',
                'awarded': awarded,
                'max_points': max_pts,
                'correct': bool(correct),
                'details': {
                    'selected_index': ai,
                    'correct_index': ci,
                    'options': q.get('options', []),
                    'text': q.get('text'),
                },
            })
            score_points += awarded
            total_points += max_pts

    percent = round((score_points / total_points) * 100, 2) if total_points else 0.0
    final_score = int(round(percent)) if percent.is_integer() else percent

    return {
        'score': final_score,
        'total': 100,
        'percent': percent,
        'raw_score': score_points,
        'raw_total': total_points,
        'per_question': per_question,
    }
