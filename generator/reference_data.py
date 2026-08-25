from __future__ import annotations

COUNTERPARTIES: list[dict] = [
    {"canonical": "Zomato Ltd", "weight": 6, "variants": ["ZOMATO LTD", "Zomato", "zomato@icici", "ZOMATO PVT LTD"]},
    {"canonical": "Swiggy Bundl Technologies", "weight": 6, "variants": ["SWIGGY", "Bundl Technologies Pvt Ltd", "swiggy@hdfc"]},
    {"canonical": "Freshworks Inc", "weight": 4, "variants": ["FRESHWORKS INC", "Freshworks", "freshworks@axis"]},
    {"canonical": "Razorpay Software Pvt Ltd", "weight": 3, "variants": ["RAZORPAY SOFTWARE", "Razorpay"]},
    {"canonical": "Tata Consultancy Services", "weight": 5, "variants": ["TCS", "Tata Consultancy Services Ltd", "TATA CONSULTANCY SERVICES"]},
    {"canonical": "Infosys BPM Ltd", "weight": 4, "variants": ["INFOSYS BPM", "Infosys BPM"]},
    {"canonical": "BigBasket Supermarket Pvt Ltd", "weight": 5, "variants": ["BIGBASKET", "Bigbasket", "bigbasket@sbi"]},
    {"canonical": "Nykaa E-Retail Pvt Ltd", "weight": 3, "variants": ["NYKAA E-RETAIL", "Nykaa"]},
    {"canonical": "Urban Company", "weight": 4, "variants": ["URBAN COMPANY", "UrbanClap Technologies India Pvt Ltd", "urbancompany@yesbank"]},
    {"canonical": "Meesho Inc", "weight": 4, "variants": ["MEESHO", "Meesho", "meesho@icici"]},
    {"canonical": "Cred Avenue Pvt Ltd", "weight": 3, "variants": ["CRED AVENUE", "Cred"]},
    {"canonical": "Rahul Sharma", "weight": 2, "variants": ["RAHUL SHARMA", "R Sharma", "rahulsharma91"]},
    {"canonical": "Priya Menon Consulting", "weight": 2, "variants": ["PRIYA MENON", "Priya Menon", "priyamenonconsulting"]},
    {"canonical": "Anand Freelance Studio", "weight": 2, "variants": ["ANAND FREELANCE STUDIO", "Anand Studio"]},
    {"canonical": "Deepika Textiles Pvt Ltd", "weight": 3, "variants": ["DEEPIKA TEXTILES", "Deepika Textiles"]},
    {"canonical": "Ola Fleet Technologies", "weight": 4, "variants": ["OLA FLEET TECHNOLOGIES", "Ola Fleet", "olafleet@hdfc"]},
    {"canonical": "PharmEasy Health Solutions", "weight": 3, "variants": ["PHARMEASY", "PharmEasy", "pharmeasy@axis"]},
    {"canonical": "Lenskart Solutions Pvt Ltd", "weight": 3, "variants": ["LENSKART SOLUTIONS", "Lenskart"]},
    {"canonical": "Vikram Enterprises", "weight": 2, "variants": ["VIKRAM ENTERPRISES", "Vikram Enterp"]},
    {"canonical": "Sundaram Logistics Pvt Ltd", "weight": 3, "variants": ["SUNDARAM LOGISTICS", "Sundaram Logistics"]},
]

BANKS: list[str] = [
    "HDFC",
    "ICICI",
    "SBI",
    "Axis",
    "Kotak",
    "Yes Bank",
    "IDFC First",
]

NARRATION_TEMPLATES_UPI: list[str] = [
    "UPI/CR/{ref}/{name}/{bank}",
    "UPI-{name}-{ref}@{bank}-CREDIT",
    "UPI/{ref}/{name}/{bank}/CREDIT",
]

NARRATION_TEMPLATES_NEFT_RTGS: list[str] = [
    "NEFT CR:{utr}/{name}",
    "RTGS-{utr}-{name}-{bank}",
    "NEFT/{utr}/{name}/{bank}",
]

AMOUNT_LOGNORMAL_MEAN = 9.0
AMOUNT_LOGNORMAL_SIGMA = 0.9
