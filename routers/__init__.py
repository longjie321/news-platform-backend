"""路由模块

定义 API 接口地址（URL）和请求处理函数。
按业务模块拆分，例如 routers/news.py、routers/users.py。
每个路由处理函数接收请求 → 调用 crud 模块的函数 → 封装返回结果。
"""