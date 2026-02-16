"""LLM-powered email generator using Google Gemini."""

from __future__ import annotations

import re

from google import genai

from src.models import AppConfig, Job


def _clean_for_email(text: str) -> str:
    """Strip hashtags and excessive whitespace from text used in emails."""
    cleaned = re.sub(r"#\w+", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s*\|\s*$", "", cleaned).strip()
    cleaned = re.sub(r"^\s*\|\s*", "", cleaned).strip()
    return cleaned

SYSTEM_PROMPT = """\
You are an expert career coach and professional email writer.
Your task is to write a job application email on behalf of the candidate.

Rules:
- Write a professional, concise, and personalized application email.
- The email should be 150-250 words (body only, excluding subject).
- Open with genuine interest in the specific role and company.
- Highlight 2-3 most relevant experiences from the resume that match the job.
- Show enthusiasm without being over-the-top.
- Close with a clear call to action (e.g., available for an interview).
- Do NOT use generic filler phrases like "I am writing to express my interest".
- Do NOT include the subject line in the body.
- Use a warm but professional tone.
- Sign off with the candidate's name.
"""

EMAIL_PROMPT_TEMPLATE = """\
Write a job application email for the following position.

--- JOB DETAILS ---
Title: {job_title}
Company: {company}
Location: {location}
Description:
{description}

--- CANDIDATE RESUME ---
{resume_text}

--- CANDIDATE INFO ---
Name: {candidate_name}
Email: {candidate_email}
Phone: {candidate_phone}

Please respond in EXACTLY this format:

SUBJECT: <email subject line>

BODY:
<email body>
"""

FALLBACK_SUBJECT = "Application for {job_title} at {company}"

FALLBACK_BODY = """\
Dear Hiring Manager,

I am excited to apply for the {job_title} position at {company}. With over 4 years \
of experience as a full-stack software engineer specializing in React, Node.js, \
TypeScript, and Python, I am confident I can contribute meaningfully to your team.

In my current role at SmythOS, I build enterprise observability systems and mobile \
implementations for AI agents. Previously at IOMechs, I delivered production applications \
serving thousands of users across healthcare and education, including a Ministry of \
Health-approved counseling app.

I would welcome the opportunity to discuss how my skills align with your needs. \
My resume is attached for your review.

Best regards,
{candidate_name}
{candidate_email}
{candidate_phone}
"""


def _parse_llm_response(response_text: str) -> tuple[str, str]:
    """Parse the LLM response into (subject, body)."""
    subject = ""
    body = ""

    lines = response_text.strip().split("\n")
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("SUBJECT:"):
            subject = stripped[len("SUBJECT:"):].strip()
            body_start = i + 1
            break

    remaining_lines = lines[body_start:]
    for i, line in enumerate(remaining_lines):
        stripped = line.strip()
        if stripped.upper().startswith("BODY:"):
            remaining_lines = remaining_lines[i + 1:]
            break

    body = "\n".join(remaining_lines).strip()

    return subject, body


def generate_application_email(
    job: Job,
    resume_text: str,
    config: AppConfig,
) -> tuple[str, str]:
    """Generate a personalized application email using Gemini.

    Args:
        job: The job posting to apply for.
        resume_text: The candidate's resume as plain text.
        config: Application configuration.

    Returns:
        Tuple of (subject, body) for the email.
    """
    clean_title = _clean_for_email(job.title)
    clean_company = _clean_for_email(job.company)

    prompt = EMAIL_PROMPT_TEMPLATE.format(
        job_title=clean_title,
        company=clean_company,
        location=job.location or "Not specified",
        description=job.description[:3000] or "No description available",
        resume_text=resume_text[:4000] or "No resume provided",
        candidate_name=config.profile.name,
        candidate_email=config.profile.email,
        candidate_phone=config.profile.phone,
    )

    try:
        client = genai.Client(api_key=config.gemini.api_key)
        response = client.models.generate_content(
            model=config.gemini.model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )

        if response.text:
            subject, body = _parse_llm_response(response.text)
            if subject and body:
                subject = _clean_for_email(subject)
                print(f"  [LLM] Generated email for: {clean_title} at {clean_company}")
                return subject, body
            else:
                print(f"  [LLM] Could not parse response, using fallback.")
        else:
            print(f"  [LLM] Empty response, using fallback.")

    except Exception as e:
        print(f"  [LLM] Gemini API error: {e}. Using fallback template.")

    subject = FALLBACK_SUBJECT.format(job_title=clean_title, company=clean_company)
    body = FALLBACK_BODY.format(
        job_title=clean_title,
        company=clean_company,
        candidate_name=config.profile.name,
        candidate_email=config.profile.email,
        candidate_phone=config.profile.phone,
    )
    return subject, body
