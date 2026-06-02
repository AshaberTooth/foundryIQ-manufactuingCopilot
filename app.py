import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request

from workflow_client import run_agentic_workflow


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
)


@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        asset_id=os.getenv("DEFAULT_ASSET_ID", "PL4-CONV-001"),
        alarm_code=os.getenv("DEFAULT_ALARM_CODE", "E-217"),
        technician_issue=(
            "Packaging Line 4 is repeatedly stopping after restart with alarm E-217. "
            "Diagnose the likely cause, tell me the safest first checks, and create a draft maintenance work order."
        ),
    )


@app.route("/troubleshoot", methods=["POST"])
def troubleshoot():
    technician_issue = request.form.get("technician_issue", "").strip()
    asset_id = request.form.get("asset_id", "").strip()
    alarm_code = request.form.get("alarm_code", "").strip()

    if not technician_issue:
        return render_template(
            "index.html",
            asset_id=asset_id,
            alarm_code=alarm_code,
            error="Please enter a technician issue.",
        )

    if not asset_id:
        asset_id = os.getenv("DEFAULT_ASSET_ID", "PL4-CONV-001")

    if not alarm_code:
        alarm_code = os.getenv("DEFAULT_ALARM_CODE", "E-217")

    try:
        result = run_agentic_workflow(
            technician_issue=technician_issue,
            asset_id=asset_id,
            alarm_code=alarm_code,
        )

        return render_template(
            "index.html",
            technician_issue=technician_issue,
            asset_id=asset_id,
            alarm_code=alarm_code,
            result=result,
        )

    except Exception as exc:
        return render_template(
            "index.html",
            technician_issue=technician_issue,
            asset_id=asset_id,
            alarm_code=alarm_code,
            error=f"Agentic workflow failed: {type(exc).__name__}: {str(exc)}",
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8500, debug=True)