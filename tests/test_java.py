"""Java / Kotlin / Spring Boot: detection, exclusions, workspaces and the error watcher."""

from __future__ import annotations

from contextzip.detector import detect
from contextzip.error_parser import detect_error_block, process_buffer, strip_noise
from contextzip.filters import build_spec
from contextzip.workspaces import declared_package_dirs
from tests.helpers import TempProject

SPRING_POM = """<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
  </parent>
  <modules>
    <module>api</module>
    <!-- <module>old</module> -->
    <module>libs/core</module>
  </modules>
</project>
"""

SPRING_NPE = """\
2026-11-05T10:00:00.000+05:30  INFO 4242 --- [demo] [main] com.acme.demo.DemoApplication : Started DemoApplication in 2.1 seconds
2026-11-05T10:00:05.123+05:30 ERROR 4242 --- [demo] [nio-8080-exec-1] o.a.c.c.C.[.[.[/].[dispatcherServlet] : Servlet.service() threw exception
java.lang.IllegalStateException: Could not load user
\tat com.acme.demo.service.UserService.find(UserService.java:42) ~[classes/:na]
\tat com.acme.demo.web.UserController.get(UserController.java:20) ~[classes/:na]
\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)
\tat org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:255)
\tat org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:1089)
\tat org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:138)
Caused by: java.lang.NullPointerException: Cannot invoke "com.acme.demo.model.User.getName()" because "user" is null
\tat com.acme.demo.service.UserService.load(UserService.java:57) ~[classes/:na]
\t... 7 common frames omitted
"""

SPRING_FAILED = """\
2026-11-05T10:00:00.000+05:30  INFO 4242 --- [demo] [main] o.s.b.w.e.tomcat.TomcatWebServer : Tomcat initialized
2026-11-05T10:00:01.000+05:30 ERROR 4242 --- [demo] [main] o.s.b.d.LoggingFailureAnalysisReporter :

***************************
APPLICATION FAILED TO START
***************************

Description:

Parameter 0 of constructor in com.acme.demo.service.UserService required a bean of type 'com.acme.demo.repo.UserRepository' that could not be found.

Action:

Consider defining a bean of type 'com.acme.demo.repo.UserRepository' in your configuration.
"""

MAVEN_COMPILE = """\
[INFO] Scanning for projects...
[INFO] Compiling 12 source files with javac [debug release 21] to target/classes
[INFO] -------------------------------------------------------------
[ERROR] COMPILATION ERROR :
[INFO] -------------------------------------------------------------
[ERROR] /D:/work/demo/src/main/java/com/acme/demo/service/UserService.java:[12,5] cannot find symbol
  symbol:   class Foo
  location: class com.acme.demo.service.UserService
[INFO] 1 error
[INFO] BUILD FAILURE
[INFO] Total time:  1.234 s
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) on project demo: Compilation failure
[ERROR] /D:/work/demo/src/main/java/com/acme/demo/service/UserService.java:[12,5] cannot find symbol
[ERROR] -> [Help 1]
[ERROR] Re-run Maven using the -X switch to enable full debug logging.
"""

GRADLE_COMPILE = """\
> Task :compileJava FAILED
src/main/java/com/acme/demo/web/UserController.java:9: error: ';' expected
        return users
                    ^
1 error

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':compileJava'.
> Compilation failed; see the compiler error output for details.

* Try:
> Run with --stacktrace option to get the stack trace.

BUILD FAILED in 2s
1 actionable task: 1 executed
"""


class JavaDetectionTests(TempProject):
    def test_spring_boot_maven(self):
        self.write("pom.xml", SPRING_POM)
        r = detect(self.root)
        self.assertEqual(r.ecosystems[:2], ["Spring Boot", "Java"])
        self.assertIn("java", r.rule_modules)

    def test_gradle_kotlin(self):
        self.write("build.gradle.kts", 'plugins { kotlin("jvm") version "2.0.0" }\n')
        r = detect(self.root)
        self.assertIn("Java", r.ecosystems)
        self.assertIn("Kotlin", r.ecosystems)
        self.assertNotIn("Spring Boot", r.ecosystems)

    def test_spring_backend_react_frontend(self):
        self.write("backend/build.gradle", "id 'org.springframework.boot' version '3.3.0'\n")
        self.write("frontend/package.json", '{"dependencies": {"react": "18"}}')
        r = detect(self.root)
        self.assertIn("Spring Boot", r.ecosystems)
        self.assertIn("Node.js", r.ecosystems)
        self.assertEqual(r.sources["Spring Boot"], "backend")


class JavaRulesTests(TempProject):
    def test_excludes_build_output_keeps_sources(self):
        s = build_spec(rule_modules=["base", "java"])
        for path in [
            "target/classes/App.class", "build/libs/app.jar", ".gradle/8.7/fileHashes.bin",
            "api/target/x.txt", "mvnw", "gradlew.bat", ".mvn/wrapper/maven-wrapper.properties",
            "api/gradle/wrapper/gradle-wrapper.properties", "demo.iml", ".classpath",
            ".settings/org.eclipse.jdt.core.prefs", "HELP.md", "hs_err_pid123.log",
        ]:
            self.assertTrue(s.match_file(path), path)
        for path in [
            "pom.xml", "build.gradle.kts", "settings.gradle", "gradle.properties",
            "src/main/java/com/acme/App.java", "src/main/resources/application.yml",
            "src/main/kotlin/com/acme/App.kt", "src/test/java/com/acme/AppTest.java",
        ]:
            self.assertFalse(s.match_file(path), path)


class JavaWorkspaceTests(TempProject):
    def test_maven_modules(self):
        self.write("pom.xml", SPRING_POM)
        for d in ("api", "libs/core", "old"):
            (self.root / d).mkdir(parents=True)
        got = {p.relative_to(self.root.resolve()).as_posix() for p in declared_package_dirs(self.root)}
        self.assertEqual(got, {"api", "libs/core"})

    def test_gradle_settings(self):
        self.write(
            "settings.gradle.kts",
            'rootProject.name = "demo"\ninclude(\n  ":app",\n  ":libs:core",\n)\n// include(":old")\nincludeBuild("tools")\n',
        )
        for d in ("app", "libs/core", "old", "tools"):
            (self.root / d).mkdir(parents=True)
        got = {p.relative_to(self.root.resolve()).as_posix() for p in declared_package_dirs(self.root)}
        self.assertEqual(got, {"app", "libs/core", "tools"})

    def test_groovy_settings(self):
        self.write("settings.gradle", "include 'web', 'service'\n")
        for d in ("web", "service"):
            (self.root / d).mkdir()
        self.assertEqual(len(declared_package_dirs(self.root)), 2)


class JavaErrorWatcherTests(TempProject):
    ECO = ["Spring Boot", "Java"]

    def setUp(self):
        super().setUp()
        self.write("pom.xml", SPRING_POM)
        for cls in ("service/UserService", "web/UserController", "model/User", "repo/UserRepository"):
            self.write(f"src/main/java/com/acme/demo/{cls}.java", "class X {}")

    def rel(self, paths):
        return sorted(p.relative_to(self.root.resolve()).as_posix() for p in paths)

    def test_spring_stack_trace(self):
        result = process_buffer(SPRING_NPE, self.root, self.ECO, "Spring Boot + Java")
        self.assertIsNotNone(result)
        prompt, error_txt, paths = result
        self.assertIn("Error type: IllegalStateException (caused by NullPointerException)", prompt)
        # Starts at the log line that announced it, not at the last "Caused by:"
        self.assertTrue(error_txt.splitlines()[0].startswith("2026-11-05T10:00:05.123"))
        self.assertNotIn("Started DemoApplication", error_txt)
        self.assertIn("framework frames omitted", error_txt)
        self.assertNotIn("DispatcherServlet.doDispatch", error_txt)
        self.assertEqual(
            self.rel(paths),
            [
                "src/main/java/com/acme/demo/model/User.java",
                "src/main/java/com/acme/demo/service/UserService.java",
                "src/main/java/com/acme/demo/web/UserController.java",
            ],
        )

    def test_application_failed_to_start(self):
        prompt, error_txt, paths = process_buffer(SPRING_FAILED, self.root, self.ECO, "Spring Boot")
        self.assertIn("Error type: Application failed to start", prompt)
        self.assertNotIn("Tomcat initialized", error_txt)
        self.assertIn("APPLICATION FAILED TO START", error_txt)
        self.assertEqual(
            self.rel(paths),
            ["src/main/java/com/acme/demo/repo/UserRepository.java", "src/main/java/com/acme/demo/service/UserService.java"],
        )

    def test_maven_compile_error(self):
        block, label = detect_error_block(MAVEN_COMPILE.splitlines(), self.ECO)
        self.assertEqual(label, "Compilation error")
        self.assertTrue(block.startswith("[ERROR] COMPILATION ERROR"))
        _, error_txt, paths = process_buffer(MAVEN_COMPILE, self.root, self.ECO, "Spring Boot")
        self.assertNotIn("Re-run Maven", error_txt)
        self.assertIn("src/main/java/com/acme/demo/service/UserService.java", self.rel(paths))

    def test_gradle_compile_error(self):
        block, label = detect_error_block(GRADLE_COMPILE.splitlines(), ["Java"])
        self.assertEqual(label, "Compilation error")
        self.assertTrue(block.startswith("> Task :compileJava FAILED"))
        self.assertNotIn("Run with --stacktrace", strip_noise(block, ["Java"]))
        _, _, paths = process_buffer(GRADLE_COMPILE, self.root, ["Java"], "Java")
        self.assertEqual(self.rel(paths), ["src/main/java/com/acme/demo/web/UserController.java"])

    def test_exception_in_thread_main(self):
        out = (
            'Exception in thread "main" java.lang.ArithmeticException: / by zero\n'
            "\tat com.acme.demo.service.UserService.divide(UserService.java:7)\n"
        )
        block, label = detect_error_block(out.splitlines(), ["Java"])
        self.assertEqual(label, "ArithmeticException")
        self.assertTrue(block.startswith("Exception in thread"))

    def test_python_traceback_untouched(self):
        out = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 3, in <module>\n'
            "    get()\n"
            "requests.exceptions.ConnectionError: refused\n"
        )
        # Unknown stack: every errors module is loaded, Java included.
        block, label = detect_error_block(out.splitlines(), [])
        self.assertTrue(block.startswith("Traceback"))
        self.assertEqual(label, "requests.exceptions.ConnectionError")
