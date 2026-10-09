"""Generate an explicitly synthetic bootstrap corpus; train a local LR model.
The separate benchmark uses unseen sentence templates. This small synthetic
benchmark cannot establish 94% accuracy on real-world job fraud.
"""

import argparse
import csv
import json
from pathlib import Path
import joblib

ROOT = Path(__file__).resolve().parent
ROLES = [
    "software engineer",
    "data analyst",
    "customer support specialist",
    "office administrator",
    "Python developer",
    "accountant",
    "marketing associate",
    "network engineer",
    "sales executive",
    "graphic designer",
    "data entry assistant",
    "project coordinator",
    "quality analyst",
    "research assistant",
    "operations associate",
    "machine learning intern",
]
REAL = [
    "We are hiring a {role}. Submit a resume through our official careers portal. Selection includes a technical interview. No application fees are charged.",
    "{role} opening with a written employment contract, clear responsibilities and salary range. Candidates complete two interviews with the hiring manager.",
    "Our company seeks a {role} with relevant skills. Benefits include health insurance and paid leave. Apply on the company website and meet the team.",
    "{role}: work with a supervisor on documented projects. A skills assessment and reference checks form part of our transparent hiring process.",
    "Paid {role} position. Recruitment never requires money from applicants. Shortlisted candidates receive an interview invitation from our corporate email.",
    "For our {role} opening, salary depends on experience. A degree or equivalent experience is required. Office location and employer details are published.",
    "Join as a {role}. Applications are reviewed by human resources. We provide a job description and formal offer only after interviews.",
    "Remote {role} position with normal working hours and team meetings. We supply equipment after onboarding. Do not pay registration fee to anyone.",
    "The {role} internship involves supervised training, a monthly stipend and an interview. There are no deposits or recruitment charges.",
    "We need a {role} to deliver weekly reports and collaborate with clients. Please provide your portfolio. Compensation and benefits are discussed in the interview.",
]
FAKE = [
    "{role} urgently wanted! Pay registration fee before starting. Guaranteed daily earnings with no interview required. Contact us on Telegram now.",
    "Instant {role} job guaranteed. Send money to activate your employment. Huge salary for only one hour daily, no qualifications needed.",
    "{role} vacancy: transfer a deposit to reserve your position today. The offer expires in one hour. Do not contact the official company.",
    "Earn unlimited income as a {role}. Buy the starter kit using cryptocurrency. You are selected immediately without any interview.",
    "{role} work from home. Share your OTP for salary verification. Send your bank password to our recruitment agent.",
    "Congratulations, selected as {role}! Pay processing fee to release the appointment letter. Recruitment conducted only via WhatsApp.",
    "Guaranteed {role} overseas visa. Pay training fee today. No experience or interview needed, limited slots available.",
    "{role} remote opportunity: deposit money to unlock tasks. Recruit more members to withdraw your profits. Guaranteed investment returns.",
    "Secret {role} opening with exceptional weekly income. Send bitcoin to our hiring wallet. No company address is available.",
    "Easy {role} job. Receive payments and transfer funds through your personal account. Pay security fee before receiving a contract.",
]
BENCH_REAL = [
    "Apply for {role} via the verified employer portal; an assessment precedes a scheduled panel interview. Compensation is stated in the contract.",
    "A {role} with relevant experience is needed for our team. HR reviews portfolios and conducts interviews. Applicants never send deposits.",
    "The {role} works alongside a supervisor, with paid leave and a documented salary. Recruitment fees are prohibited.",
]
BENCH_FAKE = [
    "Become a {role} immediately! Send a deposit to secure the guaranteed offer. No interview required; communicate through Telegram.",
    "{role} opportunity promising huge earnings for almost no work. Purchase activation credits and share your OTP to release profits.",
    "Pay registration fee for a guaranteed {role} vacancy. Hurry, transfer money today; qualifications do not matter.",
]


def generate_dataset(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for filename, pairs in [
        ("train.csv", [(REAL, 0), (FAKE, 1)]),
        ("benchmark.csv", [(BENCH_REAL, 0), (BENCH_FAKE, 1)]),
    ]:
        with (directory / filename).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "label"])
            writer.writeheader()
            for templates, label in pairs:
                for template in templates:
                    for role in ROLES:
                        writer.writerow(
                            {"text": template.format(role=role), "label": label}
                        )


def read_rows(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return [
            {"text": r["text"], "label": int(r["label"])} for r in csv.DictReader(f)
        ]


def initialize(data_dir=ROOT / "data", model_path=ROOT / "instance/model.joblib"):
    from ml_engine import train_bundle

    data_dir = Path(data_dir)
    model_path = Path(model_path)
    if (
        not (data_dir / "train.csv").exists()
        or not (data_dir / "benchmark.csv").exists()
    ):
        generate_dataset(data_dir)
    bundle = train_bundle(
        read_rows(data_dir / "train.csv"), read_rows(data_dir / "benchmark.csv")
    )
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path)
    return {k: v for k, v in bundle.items() if k != "pipeline"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--model-path", default=str(ROOT / "instance/model.joblib"))
    parser.add_argument("--download-nltk", action="store_true")
    args = parser.parse_args()
    if args.download_nltk:
        import nltk

        if not nltk.download("wordnet", quiet=True):
            raise SystemExit(
                "Could not download WordNet. Retry with internet access or omit --download-nltk for tokenization-only mode."
            )
    print(json.dumps(initialize(args.data_dir, args.model_path), indent=2))
