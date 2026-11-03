实验三：校园二手交易系统代码与构建证据

code/src/campus_market  实际Python应用源码（非GUI生成）
code/tests              业务、跨进程竞争和故障回滚测试
code/tools/build.py     编译/风格/测试/wheel/安装/已安装包运行流水线
code/USAGE.txt          运行及构建命令
code/DESIGN_CHANGES.txt 与实验二的实现差异
UML                     原设计图与Python/SQLite实现映射图、PlantUML源文件
evidence                原始构建输出、规模统计、AI开发记录、UML代码映射和wheel

Git远程：
https://github.com/AAAyuanweihua/software-engineering-lab1-requirements/tree/lab3
实验一main、实验二lab2均不覆盖。实验三保留规约、首个实现、测试与修复过程。

构建与运行结果以evidence/build.json和各阶段原始.txt为准。应用源码规模以
evidence/source_metrics.json为准，不包含测试、构建工具、UML或GUI生成代码。

尚待学生完成：逐行阅读采用的代码，独立撰写实验报告正文，将本人报告导出PDF后
再组装正式提交包。当前代码与证据包没有实验报告.pdf，不是完整课程提交包。
本说明是软件操作文档，不应复制为本人实验报告。
