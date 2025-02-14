import os
import sys
import csv
import re
import openpyxl
import openai
import pandas as pd
import PyPDF2
import ftfy
import unicodedata
import string
from io import StringIO

# --------------------------------------------------------------------------------
# 1) CONFIGURATION: API KEY & FILE PATHS
# --------------------------------------------------------------------------------
# Using the exact API key from your original scripts:
openai.api_key = "ADD API KEY"

# ---------- First Stage (Redaction) Paths ----------
INPUT_CSV_PATH = "/Users/beaganfamily/Documents/AI Project/Applicant Mapping File/Applicant_Mapping_File_Pathways.csv"
INTERMEDIATE_CSV_PATH = "/Users/beaganfamily/Documents/AI Project/Resume Output/Applicant_Mapping_File_Updated.csv"

# ---------- Second Stage (Scoring) Paths ----------
SKILL_MATRIX_FILE = "/Users/beaganfamily/Documents/AI Project/Jobs Skills Measurement Matrix/Job_Skills_Measurement_Matrix_V1.xlsx"
JOB_DESCRIPTION_FILE = "/Users/beaganfamily/Documents/AI Project/Job Description/Software Engineering Manager.pdf"
FINAL_OUTPUT_DIR = "/Users/beaganfamily/Documents/AI Project/Scored Resumes Output"
FINAL_OUTPUT_FILENAME = "Applicant_Mapping_File_Evaluated.csv"

# --------------------------------------------------------------------------------
# 2) TEXT CLEANING & PDF EXTRACTION UTILITIES
# --------------------------------------------------------------------------------
def fix_text_ftfy(text: str) -> str:
    """Use ftfy to fix common encoding issues (mojibake)."""
    return ftfy.fix_text(text)

def dictionary_replacements(text: str) -> str:
    """
    Replace known bad or special characters with intended ASCII equivalents.
    Extend this dictionary as you discover new anomalies.
    """
    replacements = {
        "‚Äì": "-",
        "Äì": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        "¬†": " ",
        "Ã©": "é",
        "Ã¨": "è",
        "Ã¡": "á",
        "Ã": "À",   # This can conflict if we see multiple combos
        "Ä¢": "C",
        "‘": "'",
        "’": "'",
        "“": "\"",
        "”": "\"",
        "…": "...",
        # Add more if needed
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text

def normalize_to_ascii(text: str) -> str:
    """
    Convert accented or special Unicode characters to their closest ASCII equivalents.
    Characters that can't be converted become '' (dropped).
    """
    text = unicodedata.normalize('NFKD', text)
    ascii_text = text.encode('ascii', 'ignore').decode('ascii', errors='ignore')
    return ascii_text

def filter_to_printable(text: str) -> str:
    """Remove any remaining characters not in Python's string.printable."""
    return ''.join(char for char in text if char in string.printable)

def deep_clean_text(text: str) -> str:
    """
    Comprehensive multi-step cleaning:
      1) ftfy for automatic fix
      2) Dictionary-based replacements
      3) Unicode normalization to ASCII
      4) Filter out non-printable chars
    """
    # 1) Auto-fix with ftfy
    text = fix_text_ftfy(text)
    # 2) Dictionary pass
    text = dictionary_replacements(text)
    # 3) Normalize to ASCII
    text = normalize_to_ascii(text)
    # 4) Filter out anything non-printable
    text = filter_to_printable(text)
    return text

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text from a PDF file, page by page, and clean it using deep_clean_text.
    """
    text_content = []
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                raw_page_text = page.extract_text() or ""
                cleaned_page_text = deep_clean_text(raw_page_text)
                text_content.append(cleaned_page_text)
    except Exception as e:
        print(f"[ERROR] Unable to read PDF '{pdf_path}': {e}")
        return ""
    
    return "\n".join(text_content)

# --------------------------------------------------------------------------------
# 3) GPT CALLS FOR REDACTION
# --------------------------------------------------------------------------------
def sanitize_gpt_response(response_text: str) -> str:
    """
    Remove common boilerplate phrases GPT might include, such as:
    'Certainly!', 'Sure!', or 'Here is the redacted...'
    """
    patterns = [
        r"(?i)\bcertainly!?[\s:]*",
        r"(?i)\bsure!?[\s:]*",
        r"(?i)here is the redacted.*?:",
        r"^---+",
    ]
    for pat in patterns:
        response_text = re.sub(pat, "", response_text)
    return response_text.strip()

def call_gpt_redaction(full_prompt: str) -> str:
    """
    Call the GPT-4o model for redacting personal info from resumes.
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a specialized resume-redaction assistant. "
                        "You provide only the cleansed text, without additional explanation."
                    )
                },
                {"role": "user", "content": full_prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        if 'choices' in response and response['choices']:
            raw_output = response['choices'][0]['message']['content']
            return sanitize_gpt_response(raw_output)
        else:
            return "Error: No response"
    except Exception as e:
        print(f"[ERROR] Redaction API call failed: {e}")
        return "Error: API call failed"

# --------------------------------------------------------------------------------
# 4) SKILL MATRIX, JOB DESCRIPTION, AND GPT CALLS FOR SCORING
# --------------------------------------------------------------------------------
def read_skills_from_xlsx(
    xlsx_file,
    sheet_name=None,
    skill_col_index=1,
    desc_col_index=2,
    weight_col_index=3
):
    """
    Reads an Excel file and returns a list of (skill_name, skill_description, skill_weight).
    :param sheet_name: If None, uses active sheet.
    :param skill_col_index: Column index (1-based) containing the skill name.
    :param desc_col_index: Column index (1-based) containing the skill description.
    :param weight_col_index: Column index (1-based) containing the skill weight.
    """
    print(f"[INFO] Loading workbook from '{xlsx_file}'...")
    wb = openpyxl.load_workbook(xlsx_file)

    if sheet_name:
        print(f"[INFO] Using sheet '{sheet_name}'")
        ws = wb[sheet_name]
    else:
        ws = wb.active
        print(f"[INFO] Using active sheet '{ws.title}'")

    skills_list = []
    first_row = True
    row_count = 0

    for row in ws.iter_rows(values_only=True):
        row_count += 1
        if first_row:
            # Skip header row (assuming your file has headers)
            first_row = False
            continue

        if not row or row[skill_col_index - 1] is None:
            continue

        skill_name = str(row[skill_col_index - 1]).strip()
        skill_desc = ""
        if row[desc_col_index - 1] is not None:
            skill_desc = str(row[desc_col_index - 1]).strip()

        weight_val = 1.0
        if len(row) >= weight_col_index and row[weight_col_index - 1] is not None:
            try:
                weight_val = float(row[weight_col_index - 1])
            except ValueError:
                pass

        skills_list.append((skill_name, skill_desc, weight_val))

    print(f"[INFO] Finished reading {row_count} rows from Excel.")
    print(f"       Found {len(skills_list)} skills with weights.")
    return skills_list

def read_pdf_text(pdf_path):
    """
    Reads all text from a PDF file and returns it as a single string.
    """
    try:
        with open(pdf_path, "rb") as pdf_file:
            reader = PyPDF2.PdfReader(pdf_file)
            pages_text = []
            for page in reader.pages:
                pages_text.append(page.extract_text() or "")
            return "\n".join(pages_text)
    except FileNotFoundError:
        print(f"[ERROR] PDF file not found at: {pdf_path}")
        return ""
    except Exception as e:
        print(f"[ERROR] Failed to read PDF {pdf_path}: {e}")
        return ""

def call_gpt_scoring(full_prompt):
    """
    Call the GPT-4o model for scoring the resume based on the skill matrix.
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a specialized resume-evaluation assistant. "
                        "You provide only the CSV-formatted answer, without additional explanation."
                    )
                },
                {"role": "user", "content": full_prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        if 'choices' in response and response['choices']:
            return response['choices'][0]['message']['content']
        else:
            return "Error: No response"
    except Exception as e:
        print(f"[ERROR] Scoring API call failed: {e}")
        return "Error: API call failed"

def extract_csv_from_backticks(response_str):
    """
    Extracts the first match of text contained within triple backticks (```...```).
    """
    matches = re.findall(r'```(?:csv|plaintext)?\s*(.*?)\s*```', response_str, flags=re.DOTALL)
    if matches:
        content = matches[0]
        single_line = " ".join(content.splitlines())
        return single_line.strip()
    else:
        return response_str

def parse_csv_line(csv_string):
    """
    Given a CSV-formatted string (ideally a single line), return a list of parsed fields.
    """
    f = StringIO(csv_string)
    reader = csv.reader(f)
    rows = list(reader)
    if rows:
        return rows[0]  # Expecting exactly one row
    return []

# --------------------------------------------------------------------------------
# 5) MAIN LOGIC
# --------------------------------------------------------------------------------
def main():
    """
    1) Redact PDF text -> produce an intermediate CSV with 'cleansed' column.
    2) Read skill matrix & job description -> read cleansed resumes -> run scoring.
    3) Produce final CSV with skill scores, summary, overall score.
    """
    # -------------------------------------------------------------------
    # STEP A: REDACT AND PRODUCE INTERMEDIATE CSV
    # -------------------------------------------------------------------
    print(f"[INFO] Reading CSV from: {INPUT_CSV_PATH}")
    df = pd.read_csv(INPUT_CSV_PATH, encoding="utf-8")
    print(f"[INFO] Total rows found in input CSV: {len(df)}")

    raw_texts = []
    cleansed_texts = []

    for index, row in df.iterrows():
        pdf_path = row.get("Filename", "")
        applicant_name = row.get("Applicant Name", "Unknown Applicant")

        print(f"\n[INFO] Processing row {index + 1}/{len(df)}")
        print(f"       Applicant Name: {applicant_name}")
        print(f"       PDF Path:      {pdf_path}")

        # 1) Extract raw text
        raw_pdf_text = extract_text_from_pdf(pdf_path)
        raw_texts.append(raw_pdf_text)
        print(f"       - Raw text length: {len(raw_pdf_text)} characters")

        # 2) Call GPT for redaction
        redaction_prompt = f"""
        DO NOT provide any introduction like "Certainly!" or "Here is...".
        Return ONLY the cleansed and restructured resume text. No disclaimers.

        Original Resume Text:
        {raw_pdf_text}

        TASK:
        1) Remove personal identifying info (Location, name, phone, address).
        2) Keep the rest of the content intact but restructure for clarity.
        3) Return only the cleansed text with no extra headings, disclaimers, or preamble.
        """

        cleansed_resume = call_gpt_redaction(redaction_prompt)
        cleansed_texts.append(cleansed_resume)
        print("       - Redaction complete. Received cleansed text.")

    # Add new columns
    df["raw_resume_outputs"] = raw_texts
    df["cleansed"] = cleansed_texts

    # Save intermediate CSV
    print(f"\n[INFO] Saving updated CSV to: {INTERMEDIATE_CSV_PATH}")
    df.to_csv(INTERMEDIATE_CSV_PATH, index=False)
    print("[INFO] Redaction step complete.")

    # -------------------------------------------------------------------
    # STEP B: SCORING
    # -------------------------------------------------------------------
    # 1) Read skill matrix from XLSX
    print("\n[INFO] Reading skills from XLSX...")
    skills_data = read_skills_from_xlsx(
        xlsx_file=SKILL_MATRIX_FILE,
        sheet_name=None,      # or specify sheet name
        skill_col_index=1,    # adjust as needed
        desc_col_index=2,     # adjust as needed
        weight_col_index=3    # adjust as needed
    )
    skill_names = [s[0] for s in skills_data]
    skill_weights = [s[2] for s in skills_data]

    # 2) Read job description PDF
    print("[INFO] Reading job description PDF...")
    job_description_text = read_pdf_text(JOB_DESCRIPTION_FILE)
    if not job_description_text:
        print("[WARNING] Job description is empty or not found. Proceeding anyway.")

    # Build textual skill matrix content
    skill_matrix_lines = []
    for skill_name, skill_desc, skill_weight in skills_data:
        skill_matrix_lines.append(f"{skill_name} (weight={skill_weight}): {skill_desc}")
    skill_matrix_content = "\n".join(skill_matrix_lines)

    # Prompt template for scoring
    prompt_template = """\
You are an expert at evaluating candidate resumes for the following role:

JOB DESCRIPTION:
{job_description}

SKILL MATRIX:
{skill_matrix}

CANDIDATE RESUME:
{candidate_resume}

Instructions:
1. Carefully review the candidate resume in the context of the JOB DESCRIPTION and the SKILL MATRIX.
2. Assign a score from 0–100 for each of the listed skills, reflecting how well the candidate meets that skill.
3. Provide a single line of CSV with columns:
   Application ID, [Skill1 Score], [Skill2 Score], ..., Summary
   - The skill columns must match the skill matrix order.
   - "Summary" is a short paragraph (~200 words) explaining:
       - Why each skill score was assigned
       - Reference the key aspects of the job description
       - Provide an overall impression of fit for the role

Application ID: {application_id}

Output ONLY one line of CSV (no extra commentary or markup).
"""

    # 3) Read the intermediate CSV with cleansed resumes
    try:
        df2 = pd.read_csv(INTERMEDIATE_CSV_PATH, encoding="utf-8")
    except FileNotFoundError:
        print(f"[ERROR] Intermediate CSV not found: {INTERMEDIATE_CSV_PATH}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Unexpected error reading the intermediate CSV: {e}")
        sys.exit(1)

    # Convert to dict-rows so we can iterate easily
    rows = df2.to_dict(orient='records')

    # We'll append new columns: <SkillName>_Score for each skill + "Summary" + "Overall_Score"
    new_columns = [f"{s}_Score" for s in skill_names] + ["Summary", "Overall_Score"]

    # Prepare final CSV columns (original + new)
    original_columns = list(df2.columns)
    final_columns = original_columns + new_columns

    # Ensure output directory exists
    os.makedirs(FINAL_OUTPUT_DIR, exist_ok=True)
    final_csv_path = os.path.join(FINAL_OUTPUT_DIR, FINAL_OUTPUT_FILENAME)

    # 4) Scoring each applicant
    print(f"[INFO] Evaluating {len(rows)} applicants...")
    with open(final_csv_path, "w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=final_columns)
        writer.writeheader()

        for i, row_dict in enumerate(rows):
            app_id = row_dict.get("Application ID", "")
            candidate_resume_text = row_dict.get("cleansed", "")

            print(f"\n[INFO] Scoring applicant {i+1}/{len(rows)} - ID={app_id}...")

            prompt = prompt_template.format(
                job_description=job_description_text,
                skill_matrix=skill_matrix_content,
                candidate_resume=candidate_resume_text,
                application_id=app_id
            )

            # Call GPT for scoring
            gpt_response = call_gpt_scoring(prompt)
            cleaned_response = extract_csv_from_backticks(gpt_response)

            print(f"      GPT raw response (first 100 chars): {gpt_response[:100]}...")
            print(f"      Cleaned response (first 100 chars): {cleaned_response[:100]}...")

            # Parse CSV line
            parsed_fields = parse_csv_line(cleaned_response)
            expected_length = 1 + len(skill_names) + 1  # = AppID + Nskills + Summary

            if len(parsed_fields) == expected_length:
                # Assign skill scores
                for idx_s, skill_name in enumerate(skill_names, start=1):
                    score_str = parsed_fields[idx_s]
                    row_dict[f"{skill_name}_Score"] = score_str
                # Summary is last
                summary_str = parsed_fields[-1]
                row_dict["Summary"] = summary_str

                # Compute overall weighted score
                skill_scores = []
                for idx_s, skill_name in enumerate(skill_names, start=1):
                    score_str = parsed_fields[idx_s]
                    try:
                        val = float(score_str)
                    except ValueError:
                        val = 0.0
                    skill_scores.append(val)

                sum_of_weights = sum(skill_weights)
                weighted_sum = 0.0
                for sc_val, w_val in zip(skill_scores, skill_weights):
                    weighted_sum += sc_val * w_val

                if sum_of_weights > 0:
                    overall_score = weighted_sum / sum_of_weights
                else:
                    overall_score = 0.0

                row_dict["Overall_Score"] = f"{overall_score:.2f}"
            else:
                # If GPT returned something unexpected
                row_dict["Summary"] = f"ERROR: Invalid AI response: {gpt_response}"
                row_dict["Overall_Score"] = "0.00"

            # Write the updated row
            writer.writerow(row_dict)

    print("\n[INFO] Scoring step complete.")
    print(f"[INFO] Final output saved to: {final_csv_path}")

# --------------------------------------------------------------------------------
# 6) EXECUTE SCRIPT
# --------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
