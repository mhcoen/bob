# mcloop.json examples

Copy one of these to `mcloop.json` in your project root and adjust as needed.
Use the `checks` array for explicit commands. Use the `detect` array for
marker-based auto-detection. Both can be combined in a single file.

## Python

```json
{
  "checks": [
    "ruff check .",
    "ruff format --check .",
    "pytest"
  ]
}
```

## Swift

```json
{
  "checks": [
    "swift build",
    "swift test"
  ]
}
```

## JavaScript / TypeScript (Node)

```json
{
  "checks": [
    "npm run lint",
    "npm test"
  ]
}
```

## Rust

```json
{
  "checks": [
    "cargo clippy -- -D warnings",
    "cargo test"
  ]
}
```

## Go

```json
{
  "checks": [
    "go vet ./...",
    "go test ./..."
  ]
}
```

## Java (Gradle)

```json
{
  "checks": [
    "gradle check",
    "gradle test"
  ]
}
```

## C / C++ (CMake)

```json
{
  "checks": [
    "cmake --build build",
    "ctest --test-dir build"
  ]
}
```

## Ruby

```json
{
  "checks": [
    "rubocop",
    "bundle exec rspec"
  ]
}
```

## Multi-language (auto-detection)

For projects that may contain multiple languages, use `detect` rules
instead of explicit checks:

```json
{
  "detect": [
    {"marker": "Cargo.toml", "commands": ["cargo clippy -- -D warnings", "cargo test"]},
    {"marker": "go.mod", "commands": ["go vet ./...", "go test ./..."]},
    {"marker": "build.gradle", "commands": ["gradle check"]},
    {"marker": "CMakeLists.txt", "commands": ["cmake --build build", "ctest --test-dir build"]},
    {"marker": "Gemfile", "commands": ["bundle exec rspec"]},
    {"marker": "Package.swift", "commands": ["swift build"]},
    {"marker": "Makefile", "commands": ["make check"]}
  ]
}
```
