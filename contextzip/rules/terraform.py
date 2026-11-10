"""
Terraform / OpenTofu / Terragrunt exclusion rules.

State files and .terraform/ are already left out everywhere (rules/base.py).
Variable files are left out here because they usually hold real values.
"""

PATTERNS = [
    # Variable files with real values (terraform.tfvars.example still goes in)
    "*.tfvars",
    "*.tfvars.json",
    # Lockfile, plans and caches
    ".terraform.lock.hcl",
    "*.tfplan",
    "tfplan",
    ".terragrunt-cache/",
    "crash.log",
    "crash.*.log",
    # Local overrides
    "override.tf",
    "override.tf.json",
    "*_override.tf",
    "*_override.tf.json",
    ".terraformrc",
    "terraform.rc",
]
