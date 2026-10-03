"""Generic, public vocabulary: nothing here describes a person. Only what a job description says."""

# Named technologies and platforms a posting may require. A term found in a posting becomes one Platform/Stack
# requirement, classified against the private profile (unknown to the profile = Unsupported).
PLATFORMS = (
    "adobe commerce", "magento", "shopify", "shopify plus", "bigcommerce", "salesforce commerce cloud", "commercetools",
    "vtex", "woocommerce", "sap commerce", "hybris", "oracle commerce", "aem", "adobe experience manager", "aep",
    "adobe experience platform", "sitecore", "contentful", "contentstack", "pimcore", "drupal", "wordpress", "episerver",
    "optimizely", "netsuite", "sap", "oracle", "oracle fusion", "microsoft dynamics", "dynamics 365", "workday",
    "infor", "epicor", "acumatica", "odoo", "salesforce", "hubspot", "marketo", "pardot", "braze", "klaviyo", "segment.com", "zuora", "chargebee", "recurly", "stripe", "adyen", "braintree", "paypal", "avalara", "vertex o series", "taxjar", "wms",
    "oms", "pim", "cdp", "dam platform", "erp", "crm", "cms", "plm", "tms", "mdm", "edi", "snowflake", "databricks", "bigquery",
    "redshift", "tableau", "power bi", "looker", "mulesoft", "boomi", "workato", "celigo", "informatica", "talend",
    "kafka", "rabbitmq", "graphql", "rest api", "restful", "soap", "grpc", "oauth", "sso", "saml", "aws", "azure", "gcp",
    "google cloud", "kubernetes", "docker", "terraform", "ansible", "jenkins", "github actions", "gitlab", "circleci",
    "datadog", "splunk", "new relic", "pagerduty", "servicenow", "jira", "confluence", "asana", "monday.com",
    "smartsheet", "ms project", "microsoft project", "azure devops", "rally software", "workfront", "clarity ppm", "figma",
    "python", "java", "javascript", "typescript", "node.js", "react", "angular", "vue", "golang", "rust", "c#",
    ".net", "php", "ruby", "rails", "django", "spring boot", "sql", "mysql", "postgresql", "mongodb", "dynamodb", "redis",
    "elasticsearch", "snowpark", "airflow", "dbt", "spark", "hadoop", "tensorflow", "pytorch", "llm", "openai",
    "generative ai", "machine learning", "retrieval augmented generation", "agile", "scrum", "safe agile", "scaled agile", "kanban", "waterfall", 
    "lean six sigma", "devops", "ci/cd", "microservices", "headless",
    "composable commerce", "mach alliance", "saas", "paas", "iaas", "api", "sdk", "etl", "ipaas", "rpa", "blockchain",
    "tealium", "google analytics", "adobe analytics", "amplitude", "mixpanel", "optimizely web", "launchdarkly",
)

# Off-target job families: a posting dominated by these is a different profession. They carry Unsupported weight in
# the Role/Title bucket (a "software engineer" duty is never Direct for a program/product profile).
OFF_TARGET = (
    "software engineer", "software developer", "backend engineer", "frontend engineer", "full stack", "full-stack",
    "data scientist", "data engineer", "machine learning engineer", "devops engineer", "site reliability",
    "security engineer", "penetration", "account executive", "sales representative", "business development rep",
    "recruiter", "sourcer", "graphic designer", "ux designer", "financial analyst", "accountant", "controller",
    "marketing specialist", "customer support", "support engineer", "customer success representative", "paralegal",
    "nurse", "physician", "electrical engineer", "mechanical engineer", "civil engineer", "construction",
)

# Soft requirements any working professional meets; counted Direct unless the profile says baseline = [].
BASELINE = (
    "communication", "collaborat", "leadership", "problem solving", "problem-solving", "stakeholder", "fast-paced",
    "detail oriented", "detail-oriented", "self-starter", "organized", "organizational", "analytical", "presentation",
    "interpersonal", "adaptab", "time management", "prioriti", "written and verbal", "bachelor", "degree",
)

# Technologies young enough that "N years" beyond their age is an inflated requirement (first public year).
TECH_FIRST_YEAR = {
    "kubernetes": 2014, "terraform": 2014, "docker": 2013, "react": 2013, "llm": 2020, "generative ai": 2022,
    "openai": 2020, "dbt": 2016, "snowflake": 2015, "databricks": 2013, "graphql": 2015, "headless": 2016,
    "composable commerce": 2020, "mach alliance": 2020, "retrieval augmented generation": 2020, "github actions": 2019, "vue": 2014, "dynamics 365": 2016,
    "shopify plus": 2014, "commercetools": 2006, "aep": 2019, "adobe experience platform": 2019,
}

# A bullet counts as a requirement only if it says one (this drops benefits, legal and company boilerplate).
SHAPE = (r"experience|proficien|knowledge|ability|understanding|familiar|background|skilled|expertise|degree|"
         r"certif|track record|demonstrated|proven|must|required|capab|fluen|command of|working with|hands-on|"
         r"years|exposure")
# Cues that a requirement is a wish, not a gate.
OPTIONAL = (r"preferred|nice to have|nice-to-have|a plus|bonus|ideally|desirable|desired|is a plus|would be great|"
            r"not required|helpful|advantageous|an advantage|familiarity")

# Section headings (matched on a short line).
HEADINGS = (
    ("preferred", r"preferred|nice to have|nice-to-have|bonus|a plus|desired|desirable|extra credit|great to have"),
    ("required", r"requirement|must[- ]have|minimum|basic qualification|qualification|what you.?ll bring|what you bring|"
                 r"what we.?re looking for|you have|you.?ll need|who you are|about you|your background|skills|"
                 r"experience|required"),
    ("summary", r"^(?:about )?the (?:role|job|position|opportunity|team)$|^about the (?:role|job|position|team)|^overview|^summary|"
                r"^job description|^position summary|^role summary"),
    ("duty", r"responsibilit|what you.?ll do|what you will do|duties|day.to.day|your impact|in this role|key tasks|"
             r"you will|your role|what you.?ll own|how you.?ll"),
    ("skip", r"benefit|perks|compensation|salary|pay range|equal opportunity|eeo|about us|about the company|our "
             r"(?:mission|values|culture)|why join|why you.?ll love|how to apply|privacy|covid|accommodation|"
             r"who we are|what we offer|total rewards|transparency|diversity|inclusion|disclaimer|^pay\\b|^location|^equal"),
)

# Generic exclusion defaults. Rules: terms (word match) and/or patterns (regex). `strict` = any hit anywhere is hard.
DEFAULT_EXCLUSIONS = (
    {"id": "clinical", "reason": "clinical", "terms": ["clinical"]},
    {"id": "healthcare", "reason": "healthcare", "terms": ["healthcare", "health care", "health-care"]},
    # D89: a different profession in the TITLE (or a hospital as the employer) is never a Fit, whatever generic requirements the posting lists.
    {"id": "hr_function", "reason": "HR / people function", "where": "title", "patterns": [
        r"\bhuman resources\b", r"\bHR\b", r"\bTA\b", r"talent acquisition", r"\brecruit\w*", r"talent partner", r"people operations",
        r"people experience", r"people partner", r"people (?:&|and) culture", r"employee benefits", r"\bpayroll\b", r"total rewards"]},
    {"id": "care_hospitality", "reason": "care / hospitality / trades role", "where": "title", "patterns": [
        r"\bnurser(?:y|ies)\b", r"child ?care", r"back[- ]?up care", r"\bhousekeep\w*", r"\bchef\b", r"\bcook\b", r"\bkitchen\b",
        r"\bnanny\b", r"\bteacher\b", r"\bteaching assistant\b", r"\bjanitor\b", r"\bcustodian\b", r"\bcleaner\b", r"\bdriver\b",
        r"\bwarehouse\b", r"\bbarista\b", r"\bcashier\b", r"\bwaiter\b|\bwaitress\b", r"\bhospitality\b", r"\bpractitioner\b"]},
    {"id": "sales_role", "reason": "sales / account / customer-facing role", "where": "title", "patterns": [
        r"(?<!pre )(?<!pre-)\bsales\b", r"\baccount (?:executive|manager|director)\b", r"\bbusiness development\b", r"\bBDR\b", r"\bSDR\b",
        r"\bcustomer success\b", r"\bcustomer support\b", r"\bcollections?\b"]},
    {"id": "language_requirement", "reason": "a language other than English is required", "where": "title", "patterns": [
        r"\b(?:spanish|german|french|italian|portuguese|polish|greek|dutch|arabic|mandarin|cantonese|japanese|korean|swedish|danish|norwegian|finnish|"
        r"turkish|russian|hindi|czech|hungarian|romanian|hebrew)[- ](?:speaking|speaker|language|fluent)"]},
    {"id": "education_role", "reason": "education role", "where": "title", "patterns": [
        r"\bstudents?\b", r"\bcurriculum\b", r"\bfaculty\b", r"\bk-?12\b", r"\bhigher ed\w*", r"\bellucian\b", r"\benrol?lment\b",
        r"\badmissions?\b", r"\bprofessor\b", r"\bacademic\b", r"\binstructor\b", r"\btutor\b"]},
    {"id": "school_employer", "reason": "school employer", "where": "company", "patterns": [r"\bschools?\b", r"\bacademy\b"]},
    {"id": "medical_role", "reason": "medical / clinical role", "where": "title", "patterns": [
        r"\bsurgical\b", r"\bsurgery\b", r"\bpatient\w*", r"\bnursing\b", r"\bnurse\b", r"\bphysician\b", r"\bhospital\b",
        r"\bdental\b", r"\bpharmac\w+", r"\btherapist\b", r"\bradiolog\w+", r"\bmedical (?:assistant|director|records)\b"]},
    {"id": "healthcare_employer", "reason": "hospital / clinic employer", "where": "company", "patterns": [
        r"\bclinic\b", r"\bhospitals?\b", r"health system", r"medical center", r"\bhealthcare\b", r"health care"]},
    {"id": "clearance", "reason": "security clearance", "strict": True, "patterns": [
        r"(?:active|current|valid|secret|top secret|ts/sci|dod|government|security|public trust)\s+(?:security\s+)?clearance",
        r"clearance\s+(?:is\s+)?(?:required|needed|mandatory)", r"\bts\s*/\s*sci\b", r"must\s+(?:be able to\s+)?obtain\s+a?\s*clearance"]},
    {"id": "federal", "reason": "federal/DoD", "terms": ["department of defense", "dod", "federal government",
                                                          "federal agency", "u.s. government", "us government"]},
)

# Professions that are a different job, not a stray requirement (Fit caps at 40 only when the posting IS one of these).
HARD_FAMILY = (
    "software engineer", "software developer", "backend engineer", "frontend engineer", "full stack engineer",
    "hands-on software development", "machine learning engineer", "model development", "data scientist", "data science",
    "devops engineer", "site reliability", "infrastructure engineer", "cloud engineer", "database administrator",
    "sap basis", "salesforce administrator", "security engineer", "security architect", "penetration", "accountant",
    "accounting", "financial modeling", "financial analyst", "chief marketing officer", "cmo",
)

# Generic shape of each dimension (a requirement belongs to exactly one, first match wins in this order).
DIM_ROLE = (r"\b\d+\s*\+?\s*(?:years?|yrs?)\b|\bsenior\b|\bprincipal\b|\bdirector\b|\bhead of\b|\bexecutive accountab\w+|\bc-?level\b|"
            r"\bvp\b|\bvice president\b|\bmentor|\bpeople management\b|\bmanage (?:a )?team|\bportfolio\b|\baccountab\w+|\bauthority\b")
DIM_TECH = (r"\bplatform\b|\barchitect\w*|\bapis?\b|\bintegrat\w+|\bcloud\b|\binfrastructure\b|\bsoftware\b|\bsystems?\b|"
            r"\bsaas\b|\btechnical\b|\btechnology\b|\bstack\b|\bdata\b|\bmigrat\w+|\breplatform\w*|\bmodernization\b")
DIM_DELIVERY = (r"multi-?(?:team|vendor|market|country|region|organi[sz]ation)|\bglobal\b|\benterprise\b|large[- ]scale|"
                r"\bcomplex\w*|ambigu\w+|distressed|high-?risk|at scale|\bconcurrent\b|multiple (?:teams|vendors|markets|organi[sz]ations)")
