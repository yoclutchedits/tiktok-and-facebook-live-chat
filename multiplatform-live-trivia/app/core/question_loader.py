"""Question loader and validation module."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from app.core.matcher import normalize_text
from app.models import AnswerType, Question


def validate_and_parse_questions(raw_data: List[dict[str, Any]]) -> List[Question]:
    """Validate question list and return parsed Question objects."""
    if not isinstance(raw_data, list) or len(raw_data) == 0:
        raise ValueError("Questions list missing or empty")

    seen_questions: set[str] = set()
    questions: List[Question] = []

    for item in raw_data:
        if not isinstance(item, dict):
            continue

        q_text = item.get("question")
        if q_text is None or not str(q_text).strip():
            raise ValueError("Question text missing or empty")

        q_text_str = str(q_text).strip()
        norm_q = normalize_text(q_text_str)

        if norm_q in seen_questions:
            raise ValueError(
                f"Duplicate normalized question: '{q_text_str}'"
            )

        seen_questions.add(norm_q)

        # --------------------------------------------------------------
        # Answer type
        # --------------------------------------------------------------
        ans_type_raw = item.get("answer_type")
        ans_type = None

        if ans_type_raw:
            if isinstance(ans_type_raw, str):
                try:
                    ans_type = AnswerType[ans_type_raw.upper()]
                except KeyError:
                    try:
                        ans_type = AnswerType(ans_type_raw.lower())
                    except ValueError:
                        ans_type = None
            elif isinstance(ans_type_raw, AnswerType):
                ans_type = ans_type_raw

        # --------------------------------------------------------------
        # Answers
        # --------------------------------------------------------------
        raw_answers = item.get("answers")

        if not raw_answers or not isinstance(raw_answers, (list, tuple)):
            raise ValueError(
                f"Answers list missing or empty for question '{q_text_str}'"
            )

        seen_answers: set[str] = set()
        clean_answers: list[str] = []

        for ans in raw_answers:
            ans_str = str(ans).strip()

            if not ans_str:
                raise ValueError(
                    f"Answer text missing or empty in question '{q_text_str}'"
                )

            norm_ans = normalize_text(ans_str)

            if norm_ans in seen_answers:
                raise ValueError(
                    f"Duplicate answer: '{ans_str}'"
                )

            seen_answers.add(norm_ans)
            clean_answers.append(ans_str)

        # --------------------------------------------------------------
        # MCQ options
        # --------------------------------------------------------------
        options: list[tuple[str, str]] = []

        if ans_type == AnswerType.MCQ:
            raw_options = item.get("options")

            if not isinstance(raw_options, dict) or not raw_options:
                raise ValueError(
                    f"MCQ options missing or empty for question '{q_text_str}'"
                )

            seen_option_labels: set[str] = set()
            seen_option_text: set[str] = set()

            for label, option_text in raw_options.items():
                label_str = str(label).strip().upper()
                option_text_str = str(option_text).strip()

                if not label_str:
                    raise ValueError(
                        f"MCQ option label missing for question '{q_text_str}'"
                    )

                if not option_text_str:
                    raise ValueError(
                        f"MCQ option text missing for question '{q_text_str}'"
                    )

                norm_label = normalize_text(label_str)
                norm_text = normalize_text(option_text_str)

                if norm_label in seen_option_labels:
                    raise ValueError(
                        f"Duplicate MCQ option label: '{label_str}'"
                    )

                if norm_text in seen_option_text:
                    raise ValueError(
                        f"Duplicate MCQ option text: '{option_text_str}'"
                    )

                seen_option_labels.add(norm_label)
                seen_option_text.add(norm_text)

                options.append((label_str, option_text_str))

            option_labels = {
                label.upper()
                for label, _ in options
            }

            option_texts = {
                normalize_text(text)
                for _, text in options
            }

            for answer in clean_answers:
                if (
                    answer.upper() not in option_labels
                    and normalize_text(answer) not in option_texts
                ):
                    raise ValueError(
                        f"MCQ correct answer '{answer}' does not match "
                        f"an option label or option text for question "
                        f"'{q_text_str}'"
                    )

        questions.append(
            Question(
                question=q_text_str,
                answers=tuple(clean_answers),
                answer_type=ans_type,
                options=tuple(options),
            )
        )

    return questions

def load_questions(file_path: str | Path) -> List[Question]:
    """Load and validate questions from JSON file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return validate_and_parse_questions(data)


class QuestionLoader:
    @staticmethod
    def load(file_path: str | Path) -> List[Question]:
        return load_questions(file_path)

    @staticmethod
    def parse(data: List[dict[str, Any]]) -> List[Question]:
        return validate_and_parse_questions(data)