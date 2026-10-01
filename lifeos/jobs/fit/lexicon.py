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
    "generative ai", "machine learning", "retrieval augmented generation", "agile", "scrum", "safe agile", "scaled agile", "kanban", "waterfall", "prince2", "itil",
    "six sigma", "lean six sigma", "pmp", "csm", "cspo", "togaf", "pmi", "devops", "ci/cd", "microservices", "headless",
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
    {"id": "clearance", "reason": "security clearance", "strict": True, "patterns": [
        r"(?:active|current|valid|secret|top secret|ts/sci|dod|government|security|public trust)\s+(?:security\s+)?clearance",
        r"clearance\s+(?:is\s+)?(?:required|needed|mandatory)", r"\bts\s*/\s*sci\b", r"must\s+(?:be able to\s+)?obtain\s+a?\s*clearance"]},
    {"id": "federal", "reason": "federal/DoD", "terms": ["department of defense", "dod", "federal government",
                                                          "federal agency", "u.s. government", "us government"]},
)
