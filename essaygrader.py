"""
Essay grading helper using Jev System 1 via TypeSafe SDK.

This module exposes:
    - jev_grader(essay: str, requirements: list[str]) -> dict
    - grade_essay(essay: str, requirements: list[str], ...) -> dict
    - get_jev_questions(rubric: list[str]) -> list[str]

It evaluates student essays against atomic rubric questions using Jev's Noul
primitive and produces structured feedback with Yes/No answers based on a
0.5 threshold, scaling grades to a maximum of 100 points.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from pydantic import BaseModel
from typesafe_sdk import TypeSafeClient, Noul
from openai import OpenAI

load_dotenv()

open_router_key = os.getenv("OPEN_ROUTER_KEY")
openai_api_key = os.getenv("OPENAI_API_KEY")

llmclient = OpenAI(api_key=openai_api_key) if openai_api_key else OpenAI()


class AtomicQuestions(BaseModel):
    questions: list[str]


@dataclass
class GradeResult:
    """Structured result returned by :func:`grade_essay`."""
    grade: int
    reasons: List[str]
    coverage: List[Dict[str, Any]]
    backend: str = "jev"
    model_used: Optional[str] = None
    raw_response: Optional[str] = None
    semantic_similarity: Optional[float] = None
    domain_analysis: Optional[str] = None
    max_points: Optional[int] = 100
    deductions: Optional[List[Dict[str, Any]]] = None
    total_deductions: Optional[int] = None
    feedback: Optional[List[Dict[str, Any]]] = None
    essay: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict representation suitable for JSON serialization."""
        return {
            "grade": self.grade,
            "reasons": self.reasons,
            "coverage": self.coverage,
            "backend": self.backend,
            "model_used": self.model_used,
            "raw_response": self.raw_response,
            "semantic_similarity": self.semantic_similarity,
            "domain_analysis": self.domain_analysis,
            "max_points": self.max_points,
            "deductions": self.deductions,
            "total_deductions": self.total_deductions,
            "feedback": self.feedback,
            "essay": self.essay,
        }




def questions_to_dict(questions: list[str]) -> dict[str, Noul]:
    """Create a Noul instruction for each question, keyed by its list index."""
    return {
        f"question{index}": Noul(instructions=question)
        for index, question in enumerate(questions)
    }


def jev_grader(essay: str, requirements: List[str], max_points: int = 100) -> Dict[str, Any]:
    """
    Grades an essay using Jev System 1 via TypeSafeClient and Noul questions.
    Returns structured results where feedback consists of the Noul questions
    and their corresponding Yes/No answers based on the 0.5 threshold.
    The grade is calculated based on a max grade of 100.
    """
    if not isinstance(essay, str) or not essay.strip():
        raise ValueError("essay must be a non-empty string")
    if not isinstance(requirements, list):
        requirements = []

    atomic_questions = [r.strip() for r in requirements if r.strip()] if isinstance(requirements, list) else []


    if not atomic_questions:
        atomic_questions = ["Does the essay address the required topic?"]

    open_router_key = os.getenv("OPEN_ROUTER_KEY")
    client = TypeSafeClient(
        api_key=open_router_key,
        base_url="https://openrouter.ai/api",
    )

    try:
        r = client.system_one(
            state=f"{essay}",
            questions=questions_to_dict(atomic_questions),
        )
        answers_dict = r.answers

    except Exception as e:
        print(f"Error during Jev system_one grading: {e}")
        answers_dict = {}

    feedback: List[Dict[str, Any]] = []
    reasons: List[str] = []
    coverage: List[Dict[str, Any]] = []
    yes_count = 0
    total_score_sum = 0.0

    for index, q_text in enumerate(atomic_questions):
        key = f"question{index}"
        noul_instance = answers_dict.get(key)

        if noul_instance is not None and hasattr(noul_instance, "noul"):
            score = float(noul_instance.noul)
        else:
            score = 0.0

        total_score_sum += score
        is_yes = score >= 0.5
        answer_str = "Yes" if is_yes else "No"
        if is_yes:
            yes_count += 1

        feedback.append({
            "question": q_text,
            "answer": answer_str,
            "score": score,
            "probability": score,
        })
        reasons.append(f"{q_text}: {answer_str}")
        coverage.append({
            "requirement": q_text,
            "addressed": is_yes,
            "answer": answer_str,
            "score": score,
            "probability": score,
            "evidence": f"Score: {score:.2f}",
        })

    num_q = len(atomic_questions) if atomic_questions else 1
    grade_100 = round((yes_count / num_q) * 100) if num_q > 0 else 0
    grade_100 = max(0, min(100, int(grade_100)))

    deductions = []
    for item in feedback:
        if item["answer"] == "No":
            pts = round(100 / num_q) if num_q > 0 else 0
            deductions.append({
                "reason": f"Criterion not met: {item['question']}",
                "points": pts,
                "requirement": item["question"],
            })

    total_ded = max(0, 100 - grade_100)

    result = {
        "grade": grade_100,
        "max_points": 100,
        "essay": essay,
        "feedback": feedback,
        "reasons": reasons,
        "coverage": coverage,
        "backend": "jev",
        "questions": atomic_questions,
        "answers": {f"question{i}": fb["answer"] for i, fb in enumerate(feedback)},
        "deductions": deductions,
        "total_deductions": total_ded,
    }

    return result


def grade_essay(
    essay: str,
    requirements: List[str],
    *,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.2,
    timeout: float = 30.0,
    max_points: int = 100,
) -> Dict[str, Any]:
    """
    Grade an essay using jev_grader against explicit requirements,
    returning structured results with feedback and grade out of 100.
    """
    return jev_grader(essay, requirements, max_points=max_points)
