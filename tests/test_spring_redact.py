"""Spring Boot config files are always redacted: by key name, in URLs, on every path that zips files."""

from __future__ import annotations

import io
import zipfile

from contextzip.api import create_zip, get_files
from contextzip.applier import FileStatus, build_plan, discard_plan, execute_plan
from contextzip.packager import _write_member
from contextzip.redact import REDACTED, SPRING_VALUE, is_spring_config, redact_spring_config
from tests.helpers import TempProject

PROPS = """\
# Database
spring.datasource.url=jdbc:postgresql://localhost:5432/app
spring.datasource.username=app
spring.datasource.password=Sup3rS3cret!
spring.mail.password = mailpass99
app.jwt.secret=${JWT_SECRET:dev-only-jwt-signing-secret}
app.jwt.token-validity-seconds=3600
app.stripe.api-key=${STRIPE_KEY}
app.stripe.key: sk_not_a_real_format_123
server.ssl.key-store=classpath:keystore.p12
server.ssl.key-store-password=changeit123
spring.security.oauth2.client.registration.github.client-secret=abc123def456
spring.security.oauth2.client.provider.github.token-uri=https://github.com/login/oauth/access_token
spring.data.redis.url=redis://default:redisPw42@cache:6379
spring.datasource.hikari.data-source-properties=user=app&password=leaked;ssl=true
app.signing.private-key=line-one\\
    line-two
server.port=8080
"""

YAML = """\
spring:
  datasource:
    url: jdbc:mysql://app:dbPass123@db:3306/app?useSSL=false
    username: app
    password: "quoted-secret-1"   # from vault
  mail:
    password: 'single-quoted-2'
  security:
    oauth2:
      client:
        registration:
          google:
            client-id: 1234.apps.googleusercontent.com
            client-secret: GOCSPX-realvalue
app:
  jwt:
    secret: ${JWT_SECRET}
    expiration: 86400000
  private-key: |
    -----BEGIN KEY-----
    abcdef
    -----END KEY-----
  name: demo
  users:
    - name: admin
      password: adminpw1
---
spring:
  config:
    activate:
      on-profile: prod
  datasource:
    password: ${DB_PASSWORD:prodFallback9}
"""


class SpringConfigNameTests(TempProject):
    def test_names(self):
        for name in [
            "application.properties", "src/main/resources/application.yml",
            "application-prod.yaml", "application-local-dev.properties", "bootstrap.yml",
        ]:
            self.assertTrue(is_spring_config(name), name)
        for name in ["app.properties", "applications.yml", "application.json", "messages.properties", "application.yml.bak"]:
            self.assertFalse(is_spring_config(name), name)


class RedactPropertiesTests(TempProject):
    def setUp(self):
        super().setUp()
        self.out, self.names = redact_spring_config("application.properties", PROPS)
        self.lines = self.out.splitlines()

    def test_secrets_redacted(self):
        for leaked in [
            "Sup3rS3cret!", "mailpass99", "dev-only-jwt-signing-secret", "sk_not_a_real_format_123",
            "changeit123", "abc123def456", "redisPw42", "leaked", "line-one", "line-two",
        ]:
            self.assertNotIn(leaked, self.out, leaked)
        self.assertIn("spring.datasource.password=[REDACTED]", self.lines)
        self.assertIn("spring.mail.password = [REDACTED]", self.lines)
        self.assertIn("app.jwt.secret=${JWT_SECRET:[REDACTED]}", self.lines)
        self.assertIn("app.signing.private-key=[REDACTED]", self.lines)
        self.assertIn("spring.data.redis.url=redis://default:[REDACTED]@cache:6379", self.lines)

    def test_everything_else_kept(self):
        for kept in [
            "# Database",
            "spring.datasource.url=jdbc:postgresql://localhost:5432/app",
            "spring.datasource.username=app",
            "app.jwt.token-validity-seconds=3600",
            "app.stripe.api-key=${STRIPE_KEY}",
            "server.ssl.key-store=classpath:keystore.p12",
            "spring.security.oauth2.client.provider.github.token-uri=https://github.com/login/oauth/access_token",
            "server.port=8080",
        ]:
            self.assertIn(kept, self.lines, kept)

    def test_count(self):
        self.assertEqual(self.names.count(SPRING_VALUE), 9)

    def test_nothing_to_redact_returns_text_unchanged(self):
        text = "server.port=8080\nspring.application.name=demo\n"
        out, names = redact_spring_config("application.properties", text)
        self.assertEqual((out, names), (text, []))

    def test_crlf_kept(self):
        out, _ = redact_spring_config("application.properties", "a.password=x1234567\r\nserver.port=1\r\n")
        self.assertEqual(out, "a.password=[REDACTED]\r\nserver.port=1\r\n")


class RedactYamlTests(TempProject):
    def setUp(self):
        super().setUp()
        self.out, self.names = redact_spring_config("application.yml", YAML)
        self.lines = self.out.splitlines()

    def test_secrets_redacted(self):
        for leaked in [
            "dbPass123", "quoted-secret-1", "single-quoted-2", "GOCSPX-realvalue",
            "BEGIN KEY", "abcdef", "adminpw1", "prodFallback9",
        ]:
            self.assertNotIn(leaked, self.out, leaked)
        self.assertIn('    password: "[REDACTED]"   # from vault', self.lines)
        self.assertIn("    password: '[REDACTED]'", self.lines)
        self.assertIn("  private-key: [REDACTED]", self.lines)
        self.assertIn("      password: [REDACTED]", self.lines)
        self.assertIn("    password: ${DB_PASSWORD:[REDACTED]}", self.lines)
        self.assertIn("    url: jdbc:mysql://app:[REDACTED]@db:3306/app?useSSL=false", self.lines)

    def test_structure_and_other_values_kept(self):
        for kept in [
            "    username: app",
            "            client-id: 1234.apps.googleusercontent.com",
            "    secret: ${JWT_SECRET}",
            "    expiration: 86400000",
            "  name: demo",  # the line right after the dropped block scalar
            "    - name: admin",
            "---",
            "      on-profile: prod",
        ]:
            self.assertIn(kept, self.lines, kept)

    def test_count(self):
        self.assertEqual(self.names.count(SPRING_VALUE), 7)


class SpringPackagingTests(TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.write("pom.xml", "<project><artifactId>spring-boot-starter-parent</artifactId></project>\n")
        self.write("src/main/java/com/acme/App.java", "class App {}\n")
        self.cfg = "src/main/resources/application.properties"
        self.real = "server.port=8080\nspring.datasource.password=RealDbPass1\n"
        self.write(self.cfg, self.real)

    def package(self):
        return create_zip(get_files(self.root, use_gitignore=False))

    def zipped(self, result, rel):
        with zipfile.ZipFile(result.zip_path) as zf:
            return zf.read(rel).decode("utf-8")

    def test_redacted_by_default_and_reported(self):
        result = self.package()
        self.assertEqual(self.zipped(result, self.cfg), "server.port=8080\nspring.datasource.password=[REDACTED]\n")
        self.assertIn(self.cfg, [rel.as_posix() for rel, _ in result.redacted])
        self.assertEqual(self.read(self.cfg), self.real)  # your file is never touched

    def test_iso_8859_1_properties_still_redacted(self):
        data = "app.greeting=Grüße\napp.password=Geheim123\n".encode("latin-1")
        self.write(self.cfg, data)
        result = self.package()
        with zipfile.ZipFile(result.zip_path) as zf:
            out = zf.read(self.cfg).decode("latin-1")
        self.assertEqual(out, "app.greeting=Grüße\napp.password=[REDACTED]\n")

    def test_watcher_source_files_redacted(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            _write_member(zf, self.root / self.cfg, __import__("pathlib").Path(self.cfg), False, frozenset(), frozenset())
        with zipfile.ZipFile(buf) as zf:
            self.assertNotIn("RealDbPass1", zf.read(self.cfg).decode("utf-8"))

    def test_apply_zip_keeps_real_values(self):
        self.package()
        returned = "server.port=9090\nspring.datasource.password=[REDACTED]\n"
        z = self.make_zip("reply.zip", {self.cfg: returned})
        plan = build_plan(z, self.root)
        self.addCleanup(discard_plan, plan)
        entry = plan.entries[0]
        self.assertTrue(entry.redacted)
        self.assertEqual(entry.status, FileStatus.MODIFIED)
        execute_plan(plan, self.root)
        self.assertEqual(self.read(self.cfg), "server.port=9090\nspring.datasource.password=RealDbPass1\n")

    def test_untouched_reply_is_unchanged(self):
        self.package()
        z = self.make_zip("reply.zip", {self.cfg: "server.port=8080\nspring.datasource.password=[REDACTED]\n"})
        plan = build_plan(z, self.root)
        self.addCleanup(discard_plan, plan)
        self.assertEqual(plan.entries[0].status, FileStatus.UNCHANGED)
        self.assertEqual(REDACTED, "[REDACTED]")
