from pathlib import Path
import json,html,subprocess
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,Image
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from PIL import Image as PI
R=Path(r'D:\软件工程\实验2_241220055_王陈钰');W=Path(r'D:\软件工程\实验2_241220055_王陈钰_工作材料')
pdfmetrics.registerFont(TTFont('CN',r'C:\Windows\Fonts\simsun.ttc',subfontIndex=0));pdfmetrics.registerFont(TTFont('B',r'C:\Windows\Fonts\simhei.ttf'))
C=colors.HexColor('#17354B');T=colors.HexColor('#16766F');L=colors.HexColor('#DCE6EA')
styles={k:ParagraphStyle(k,fontName='B' if k in ['title','h','sub','th'] else 'CN',fontSize=s,leading=lead,textColor=colors.white if k=='th' else C,spaceAfter=8,wordWrap='CJK') for k,s,lead in [('title',26,35),('h',18,26),('sub',12,19),('body',10.5,17),('small',9,14),('th',9.5,14)]}
story=[]
def p(s,style='body'):return Paragraph(html.escape(s).replace('\n','<br/>'),styles[style])
def add(*x):story.extend(x)
def title(s):add(p(s,'h'))
def text(s):add(p(s))
def table(head,rows,widths):
 data=[[p(x,'th') for x in head]]+[[p(str(x),'small') for x in row] for row in rows]
 t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),C),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F0F6F5')]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.4,L)]));add(t,Spacer(1,8))
def pic(path,w=503,h=500):
 iw,ih=PI.open(path).size;scale=min(w/iw,h/ih);add(Image(str(path),width=iw*scale,height=ih*scale),Spacer(1,8))
def page():add(PageBreak())
git=r'C:\Users\16178\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe'
sha=subprocess.check_output([git,'-C',str(R),'rev-parse','HEAD']).decode().strip()
feedback=(W/'feedback.json')
accepted=feedback.exists() and json.loads(feedback.read_text(encoding='utf-8')).get('accepted',False)
add(Spacer(1,60),p('校园二手交易系统','title'),p('实验二 · 软件设计与建模','h'),Spacer(1,20))
table(['项目','内容'],[['姓名 / 学号','王陈钰 / 241220055'],['课程','软件工程'],['需求基线','实验一 lab1-final：2437587222e01c8b1486e6812fac77ca7d386106'],['设计日期','2026-11-03'],['提交截止','2026-11-06 23:59，cslab-cms'],['绘图工具','PlantUML 1.2026.9；draw.io Desktop 32.6.1'],['交付范围','8张UML图（7种类型）及源代码、12页UI及原生源文件、报告']],[100,403])
text('本实验为设计模型与界面原型，不要求完整运行的软件。图中数据库、事务、API是拟采用的设计方案，尚未声称通过软件性能或并发测试。参考作业仅用于了解提交组织方式，未复用其任务单App业务或图稿。')
text('依据用户本次新版任务书：允许AI辅助报告，但须本人核对。附件2.md的旧版报告禁用AI及旧截止日期与本轮要求不同，本报告以本轮明确要求为准。')
page();title('1  功能范围与需求追踪')
text('本项目继续实验一的校园二手交易系统。普通账号既可买又可卖，管理员通过受控初始化或邀请建立。每条商品是一件实物，仅能完成一次最终交易；采用线下交付和双方确认，不接入真实支付、物流、聊天、评价或校内认证；第一版不自动超时释放预约。')
table(['功能模块','基线需求','设计与UI'],[
['身份与授权','002–004、028–029','User与资源归属校验；UI01、10'],['商品管理与审核','005–010','Product、ReviewRecord；UI04、05、09'],['查询排序分页','011–012、032','状态可见范围、稳定排序、每页20条；UI02'],['预约与交易','013–020、022、033–034','Reservation、Cancellation、事务与回执；UI03、06–08'],['数据与统计','021、023–025','持久化关联与两类统计；UI11'],['审计与质量','026–027、030、036–038','只读日志、量化验收、异常反馈；UI12及全局控件']],[105,112,286])
text('REQ-031已并入价格校验，不重复设计；REQ-035自动超时已拒绝，模型和页面不包含自动倒计时。价格必须大于0、不超过99999.99元且至多两位小数。公开列表只展示已发布并可预约商品，卖家管理页查本人全状态、管理员按权限查全状态。')
add(p('设计选型的个人输入','sub'));text('本轮用户实际选择“采纳，重点展示事务与异常处理”，据此选择分层单体、买卖同账号、独立管理界面。这里的独立后台指表示层和权限入口分离，不是部署为独立微服务；仍使用同一应用与数据库事务。')
page();title('2  架构与模块协作设计')
text('课程规模下采用模块化分层单体：表示层发出请求，控制器调用应用服务，领域对象约束状态转换，Repository/UnitOfWork持久化。商品、交易、审核、统计和审计边界明确，但部署在同一进程，避免为跨模块状态同步引入分布式事务。')
table(['交互','设计理由'],[['控制器 → 身份授权 → 应用服务','每个写请求及私人记录读取均服务端校验，不把隐藏按钮当作安全边界。'],['治理模块 → 交易服务','违规和停用的在途关闭复用一致性入口，避免后台直接改一张表。'],['交易 → UnitOfWork → 数据库','统一锁顺序商品、预约、取消申请；业务和成功审计、请求回执一同提交。'],['统计 → 只读查询','卖家按完成时间与快照聚合；管理员按当前商品计数，避免历史预约放大计数。'],['审计 → 数据库','成功日志同事务；失败回滚后另记失败，不把失败描述为成功。']],[160,343])
add(p('3  数据存储与关键约束','h'));text('拟采用PostgreSQL关系型数据库。主要表为users、categories、products、reservations、cancellations、review_records、audit_logs和request_receipts，采用UUID主键；金额使用精确NUMERIC，时间用带时区时间戳，存储统一瞬间，展示与日期筛选转换为北京时间。图片文件由受控路径保存，数据库保存引用，不在公开输入中接收任意文件路径。')
table(['约束 / 索引','用途'],[['UNIQUE users(account)','账号唯一；密码使用适合密码存储的带盐哈希。'],['部分唯一索引 reservations(product_id) WHERE status IN (PENDING,CONFIRMED,CANCEL_PENDING)','同一商品最多一条有效占用，关闭/拒绝/取消历史仍可保留。'],['UNIQUE request_receipts(actor_id, request_key)','请求防重，保存参数摘要和原结果；同键异参返回409。'],['UNIQUE active cancellation per reservation','同一交易最多一条待处理取消申请。'],['products(category_id,status,published_at,id)；reservations(seller_id,status,completed_at)','服务筛选、稳定排序及完成交易聚合；最终索引效果须实测。']],[210,293])
page();title('4  事务、接口与异常协议')
text('预约：鉴权后锁商品，检查当前状态、所见版本、是否本人和违规限制；建立快照预约、更新商品、写回执和成功日志后提交。唯一约束兜底。相同请求键且摘要一致返回已提交结果，不再次建记录；不同摘要返回冲突。中间异常整体回滚，失败审计在事务外独立记录。')
text('双方确认：每个交易保存buyerConfirmed、sellerConfirmed和confirmationEpoch。一方确认只保存本方标记；第二方有效确认才原子完成。取消被拒绝或撤回时清空标记并递增epoch。旧页面即使仍显示已确认，也不能把上一轮确认重新提交。epoch是落实已确认需求的设计机制，不是新增业务需求。')
text('治理与完成竞争：两者均先锁同一商品，后锁预约；先提交成功者决定事实。违规先成功则关闭未完成交易；完成先成功则治理只增加违规标记，不改写完成历史。锁等待、死锁回滚必须返回可重试错误，不允许部分提交。')
table(['接口（拟定）','条件 / 返回'],[['POST /reservations','productId、seenVersion、requestKey；201成功，409版本/占用冲突'],['POST /reservations/{id}/accept | reject','仅所属卖家且当前待处理；重复回执不改变结果'],['POST /reservations/{id}/cancel-requests','本人已确认未完成；进入CANCEL_PENDING阻止完成'],['POST /cancel-requests/{id}/approve | reject | withdraw','按卖家/买家权限，核对待处理，拒绝/撤回清标记增epoch'],['POST /reservations/{id}/confirm','本人参与方、正确epoch、无取消申请；一方确认不完成'],['POST /admin/products/{id}/restrict','必填原因，同事务关闭未完成预约或只标记已完成商品']],[235,268])
text('统一错误：401未登录、403权限不足、409状态/版本/请求键冲突、422字段无效、503暂时不可用。响应含业务错误码、当前状态及requestId，不返回密码或数据库内部堆栈。前端按钮禁用只是提示，服务端必须重新检查。')
page();title('5  UML类型认识与选择')
table(['分类','认识与本项目取舍'],[['结构性','类图：对象属性、方法、关联和多重性，必画；组件图：模块依赖；部署图：运行节点。对象图用于实例快照；复合结构图用于内部端口；包图用于命名空间；剖面图用于扩展元模型。本项目前三种最能表达持久化和部署边界。'],['行为性','用例图描述参与者目标；状态图约束商品生命周期；活动图描述取消分支和处理顺序。三者分别回答谁做什么、状态如何变、过程如何分支。'],['交互性','时序图/序列图是同一类图的常见称呼，不重复计数；通信图强调对象协作网络；交互概览图组织多个交互片段。选时序图展示事务消息及失败分支，并用第二场景补充确认竞争。']],[85,418])
text('报告共8张图、7种不同UML类型：类、组件、部署、用例、状态、活动、时序。包含必画类图，并在此基础上覆盖三类。所有PNG由对应.puml使用PlantUML实际生成；并非将普通流程图改名为UML。')
notes=[
('01_类图','结构性：类图','User同账号承载买卖业务角色，不建立互斥Buyer/Seller账号子类。Product与Reservation是一对多历史关联，最多一条有效预约通过数据库约束表达，而非把历史关联错误画成一对一。Cancellation属于预约的取消历史，ReviewRecord与AuditLog保存追溯。','REQ-002、013、018、019、021、022'),
('02_组件图','结构性：组件图','控制器统一入口，应用服务按领域划分；治理关闭调用交易规则，统计只读，成功审计参与同一事务。通过分层单体保持模块清晰和原子更新，不为课程原型引入服务间分布式一致性。','REQ-003、010、025、026'),
('03_部署图','结构性：部署图','浏览器通过HTTPS连接应用；数据库连接和图片存储仅由应用控制。数据库可与应用同机，但逻辑边界分开。备份与恢复作为实现方案而非声称已完成的运维能力。','REQ-021、028、029'),
('04_用例图','行为性：用例图','买家和卖家是同一普通账号在不同场景扮演的业务角色；管理员权限明确。图中涵盖课程必需注册登录、发布审核、查询、预约、取消、确认、完成、统计、日志和治理。','REQ-002至024、026、027'),
('05_商品状态图','行为性：状态图','保持实验一七个商品主状态。取消申请是预约子状态；违规限制为附加标志。已完成遇违规只加限制。禁止的跨阶段跳转由应用层和数据库条件更新共同拒绝。','REQ-009、010、018至020、034'),
('06_取消活动图','行为性：活动图','卖家确认前可直接取消；确认后申请并等待卖家同意。拒绝或撤回恢复已确认但清空旧标记；每次动作都是短事务，不能持锁等待人处理。失败回滚并记录结果。','REQ-017、018、025'),
('07_预约时序图','交互性：时序图','显示消息顺序、锁定、回执复用、状态与版本检查及提交。相同回执不重复创建；同键异参冲突规则详见接口协议；两个买家等待同一商品行锁，后到者重新检查当前状态。','REQ-013至015、022、025、033'),
('08_双方确认时序图','交互性：时序图（补充场景）','确认操作必须本人参与方。重复回执返回原结果；旧epoch、待处理取消、已关闭交易都拒绝。第二方确认只完成一次；同一锁序解决违规与完成竞争。','REQ-018、019、025、034')]
for name,kind,desc,refs in notes:
 page();title('UML · '+kind);pic(R/'UML'/(name+'.png'),h=505);text(desc);add(p('需求依据：'+refs+'；可编辑源：UML/'+name+'.puml','small'))
page();title('6  UI设计规范与导航')
text('采用draw.io Desktop原生mxGraph控件设计12页中保真原型，每页一个模块场景，统一1440×1000画布。PNG由官方CLI实际导出，.drawio保留全部可编辑控件。此处展示交互结构、字段、状态和异常反馈，未宣称是已运行网页，也未声称使用Figma或Pixso。')
text('视觉规范：深蓝导航与青绿关键动作，浅色卡片层次；左侧稳定入口，右侧展示当前任务；重要错误同时使用文字与动作提示，不只靠颜色。普通用户不应出现管理员入口，管理界面独立导航；示意稿中的管理导航用于说明模块，并不授权普通账号访问。')
text('每页标出对应需求，示例数据不含真实交易。登录不暴露角色选择；字段错误就地提示；重复提交显示处理中；版本/状态冲突提示刷新；请求失败不显示成功结算。界面只展示业务结果，内部版本号和确认轮次不应成为用户决策负担。')
screens=json.loads((W/'screens.json').read_text(encoding='utf-8'))
for name,module,heading,refs,rows in screens:
 page();title('UI · '+module);pic(R/'UI界面'/(name+'.png'),h=410);text(heading+'。本页对应 '+refs+'。')
 text('；'.join(label+'：'+detail for label,detail in rows if 'epoch' not in detail and 'confirmation' not in detail)+ '。')
 add(p('交互入口、字段与关键状态见原型；具体操作执行前仍由服务端校验权限、当前状态及相关约束。','small'))
page();title('7  AI辅助过程与个人判断记录')
text('工具：Codex桌面应用；可核实模型自述为基于GPT-6的Codex，具体子型号未向本记录暴露，不猜测版本。每次业务用途按下表记录，生成的原始文件通过Git留存；绘图渲染、语法修复与PDF排版是工具操作，并单列在使用记录中。')
table(['使用场景 / 结果','优点与局限','修改优化及来源'],[
['AI-01 读取基线与参考\n形成模块清单与三类UML选型','能建立覆盖矩阵；附件旧版与新版冲突须由用户要求裁定。','以本轮任务书为准，不复制参考业务；用户采纳分层单体、同账号及独立后台。'],
['AI-02 生成PlantUML与draw.io原型\n7种UML、12页UI','快速建立一致结构；生成代码出现字符串换行和花括号语法问题。','实际编译后修复源文件，非故意制造错误；PNG使用官方工具重新导出。'],
['AI-03 补充事务与异常边界\n双方确认时序图、确认轮次机制','能暴露旧确认重放风险；机制需本人判断是否符合基线。','保持原业务要求不变，增加epoch作为设计机制；移除用户页面中的内部轮次，关键动作改实色按钮。'],
['AI-04 报告组织与可追踪记录\n本报告、覆盖表、渲染核验','可整合图文与版本证据；不能虚构本人审查、测试成绩或实际实现。','报告明确方案性质，记录实际反馈；Git保留初稿与优化差异，人工采纳另记。'],
],[155,160,188])
text('个人输入的真实范围：用户明确采纳“重点展示事务与异常处理”。后续具体三项设计核对状态为：'+('用户已实际确认三项均采纳，原答见设计记录。' if accepted else '已发出核对问题，尚未收到答复，不能写成已完成人工修改。'))
text('反思：AI适合把已确认需求映射到对象、页面和图形，给出多个方案及一致性检查，但不应自行决定业务政策。可采用“人确认目标与风险 → AI生成多视图 → 编译与矩阵自动检查 → 人审查事务边界、状态和UI → AI按反馈修订 → 版本留痕 → 人批准交付”的工作流。人只检查校对在格式化和低风险部分可行；在需求取舍、并发语义、异常承诺和最终验收上仍须作决定，不能把责任全部交给自动生成。')
if accepted:
 page();title('7.1  本人核对与采纳后的设计')
 text('在可编辑设计稿及报告形成后，王陈钰对三项具体机制答复“全部采纳”。本页记录本人作出采纳决定、AI据此执行归档与报告修订的实际过程；不把AI生成源代码或自动语法修复称为本人手工绘图。本人未提供具体审查时分，故仅记会话日期2026-11-03。')
 table(['核对项','设计演进及最终决定','可查证结果'],[
 ['REV2-001\n有效预约与事务','早期模型已描述预约唯一性；本人采纳事务加唯一约束的落实方案，保留统一锁序和同事务业务/回执/成功日志。','组件图、预约时序图；报告第3、4节；需求013、014、025。'],
 ['REV2-002\n取消后的旧确认','保留清空双方标记的业务要求；本人采纳递增确认轮次、拒绝旧请求的技术细化，解决旧页面重复完成风险。','类图confirmationEpoch、双方确认时序图、取消处理UI；需求018、019。'],
 ['REV2-003\n业务提示与技术信息','原UI显示epoch等内部术语；本人采纳修改为“原有确认已清除，请双方重新确认”和刷新提示。内部轮次继续用于服务端校验。','UI07、UI08及draw.io修改差异；Git初稿与优化提交可对照。'],
 ],[105,245,153])
 text('本人原始答复：全部采纳。归档时保留此前“待核对”的阶段记录，再追加采纳结论，不篡改先前生成过程。三项具体方案在审阅稿中已供检查，本次不为制造人工差异重新编造业务规则，而是将方案状态更新为已采纳，并完善报告与使用记录。')
 text('对设计的判断：事务和约束共同守住数据不变量，确认轮次将取消恢复后的旧操作与新一轮区分，业务界面则只表达用户需要理解的结果。三个层次相互配合，适合后续课程实现；是否真正可靠仍须通过数据库故障与并发测试，而非仅凭模型成立。')
page();title('8  验证、版本与后续工作')
text('本次检查包括：PlantUML语法实际渲染、图与源配对、draw.io分页导出、UI模块覆盖、需求矩阵、PDF页面及中文排版检查。没有执行后端功能测试；未来需按实验一的20并发、100次查询至少95次1秒内成功目标验证，并执行预约与取消各10次故障注入和双买家并发。')
table(['检查项','通过标准 / 当前性质'],[['需求覆盖','36条有效已确认需求映射至模块、UML或UI；031归并、035拒绝不另实现。'],['模型一致性','双方确认、取消重置、版本冲突、违规竞争、唯一有效预约和日志均可追踪。'],['UI可用性目标','后续5名首次使用者，无口头指导，3分钟完成搜索、预约、取消，至少4人全部成功。'],['源码与导出','8组.puml/.png，12张UI.png及一份12页.drawio原生源。'],['个人审查','仅记录真实用户反馈；未实际审查的内容不能称为本人已审查。']],[125,378])
text('本地Git仓库：D:/软件工程/实验2_241220055_王陈钰。初稿标签lab2-ai-draft保留首次可编译设计；本报告生成前的设计提交编号如下（不表示后续归档提交）：')
add(p(sha,'small'))
text('实验一公开需求基线：https://github.com/AAAyuanweihua/software-engineering-lab1-requirements ，lab1-final完整提交编号2437587222e01c8b1486e6812fac77ca7d386106。本轮实验二暂存本地Git；未声称已公开发布或已提交课程网站。')
text('后续工程建议：先实现User/Product/Reservation持久化与事务服务，补充分页与权限用例，再实现后台审计和统计；最后按质量指标测试。图片规格、备份频率等实现参数由设计阶段选定并在实现前记录，不能伪称是甲方已经承诺的要求。')
text('工具参考：PlantUML https://plantuml.com/download 、https://plantuml.com/smetana02；draw.io https://www.drawio.com/docs/manual/export/export-to-png/ 、https://github.com/jgraph/drawio/wiki/Desktop-App 。仅用作工具语法与导出依据。')
def frame(c,d):
 c.saveState();c.setStrokeColor(L);c.line(46,810,549,810);c.setFont('CN',8);c.setFillColor(C);c.drawString(46,818,'软件工程 · 实验二 / 校园二手交易系统');c.drawRightString(549,818,'设计与建模');c.line(46,35,549,35);c.drawString(46,23,'241220055 王陈钰  |  2026-11-03');c.drawRightString(549,23,str(d.page));c.restoreState()
SimpleDocTemplate(str(R/'实验报告.pdf'),pagesize=A4,leftMargin=46,rightMargin=46,topMargin=48,bottomMargin=48,title='校园二手交易系统：软件设计与建模',author='王陈钰').build(story,onFirstPage=frame,onLaterPages=frame)
print('report generated')
