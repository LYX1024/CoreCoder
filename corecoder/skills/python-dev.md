## Python 开发规范

- 使用 type hints 标注所有函数参数和返回值
- 遵循 PEP 8 命名规范：类名 `PascalCase`，函数/变量 `snake_case`，常量 `UPPER_CASE`
- 优先使用 `pathlib.Path` 而非 `os.path`
- 文件读写必须指定 `encoding="utf-8"`
- 异常处理尽量捕获具体异常类型，避免裸 `except:`
- 列表推导式优于 `map()`/`filter()`，但不要嵌套超过两层
- 字符串拼接使用 f-string，避免 `+` 或 `%` 格式化
