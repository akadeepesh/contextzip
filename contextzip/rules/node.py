"""
Node.js, Next.js, and general JS/TS framework exclusion rules.
"""

PATTERNS = [
    "node_modules/",
    ".next/",
    ".nuxt/",
    "dist/",
    "build/",
    "out/",
    ".turbo/",
    ".vercel/",
    ".netlify/",
    "coverage/",
    ".nyc_output/",
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    ".pnp.js",
    ".pnp.cjs",
    ".yarn/",
    "storybook-static/",
    ".storybook/",
    "*.d.ts",         # generated type declarations
    "tsconfig.tsbuildinfo",
]
