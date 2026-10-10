"""CRUD 模块

封装对数据库的增删改查（Create / Read / Update / Delete）操作。
按业务模块拆分，例如 crud/news.py、crud/users.py。
路由层（routers/）只负责调用这里的函数，不直接写 SQL。
"""