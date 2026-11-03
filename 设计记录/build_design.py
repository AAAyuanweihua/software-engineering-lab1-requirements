from pathlib import Path
import json,html,xml.etree.ElementTree as E,subprocess,shutil
W=Path(r'D:\软件工程\实验2_241220055_王陈钰_工作材料')
R=Path(r'D:\软件工程\实验2_241220055_王陈钰')
for d in ['UML','UI界面','设计记录']: (R/d).mkdir(parents=True,exist_ok=True)
common='''skinparam defaultFontName Microsoft YaHei
skinparam defaultFontSize 15
skinparam shadowing false
skinparam backgroundColor #FFFFFF
skinparam ArrowColor #396774
skinparam classBackgroundColor #F0F7F5
skinparam componentBackgroundColor #F0F7F5
skinparam stateBackgroundColor #F0F7F5
skinparam roundcorner 8
hide empty members
'''
diagrams={
'01_类图':('结构性 / 类图','''class User {
 +id: UUID
 +account: String {unique}
 -passwordHash: String
 +enabled: Boolean
 +admin: Boolean
}
class Product {
 +id: UUID
 +title: String
 +price: Decimal(7,2)
 +status: ProductStatus
 +version: Long
 +restricted: Boolean
 +publishedAt: Instant
 +changeDetails()
}
class Category { +id: UUID }
class Reservation {
 +id: UUID
 +status: ReservationStatus
 +priceSnapshot: Decimal(7,2)
 +buyerConfirmed: Boolean
 +sellerConfirmed: Boolean
 +confirmationEpoch: Long
 +completedAt: Instant?
 +confirmDelivery(actor, epoch)
}
class Cancellation {
 +id: UUID
 +status: Pending/Approved/Rejected/Withdrawn
 +reason: String
 +decide()
}
class ReviewRecord { +decision: String
 +reason: String
 +productVersion: Long }
class AuditLog { +actorId: UUID
 +action: String
 +beforeAfter: JSON
 +outcome: String
 +occurredAt: Instant }
class RequestReceipt { +actorId: UUID
 +requestKey: UUID
 +payloadHash: String
 +result: JSON }
User "1" -- "0..*" Product : 卖家
User "1" -- "0..*" Reservation : 买家
Product "1" -- "0..*" Reservation : 历史预约
Category "1" -- "0..*" Product
Reservation "1" *-- "0..*" Cancellation
Product "1" -- "0..*" ReviewRecord
User "1" -- "0..*" ReviewRecord : 审核者
User "1" -- "0..*" AuditLog : 操作者
User "1" -- "0..*" RequestReceipt
note bottom of Reservation
有效状态：PENDING、CONFIRMED、CANCEL_PENDING
每商品最多一条有效预约（数据库部分唯一索引）
取消拒绝/撤回：清空双方标记并递增confirmationEpoch
防止旧确认请求在标记重置后再次生效
end note
note bottom of Product
0 < price <= 99999.99，最多两位小数
同一User可同时买卖；不拆成互斥子类
end note
'''),
'02_组件图':('结构性 / 组件图','''top to bottom direction
package "浏览器表示层" {
 [买卖用户界面] as UserUI
 [独立管理员界面] as AdminUI
}
package "分层单体：同一部署单元" {
 [REST Controllers] as API
 [身份与资源授权] as Auth
 [商品与查询模块] as Catalog
 [交易应用服务\n预约/取消/双方确认] as Trade
 [审核与治理模块] as Gov
 [统计查询模块] as Stats
 [审计模块] as Audit
 [Repository + UnitOfWork] as Repo
}
database "PostgreSQL" as DB
folder "受控图片存储" as Images
UserUI --> API : HTTPS/JSON
AdminUI --> API : HTTPS/JSON
API --> Auth : 每请求校验
API --> Catalog
API --> Trade
API --> Gov
API --> Stats
Gov --> Trade : 同事务关闭未完成交易
Catalog --> Repo
Trade --> Repo
Gov --> Repo
Stats --> Repo : 只读聚合
Catalog --> Images
Trade --> Audit : 成功审计同事务
Gov --> Audit
Audit --> Repo
Repo --> DB : 参数化SQL/本地事务
note bottom of Repo
统一锁顺序：商品 -> 预约 -> 取消申请
同一事务更新商品、预约、回执与成功审计
失败回滚后另记失败审计，不能假装成功
end note
'''),
'03_部署图':('结构性 / 部署图','''node "用户设备" { artifact "浏览器\n买卖界面 / 管理界面" as Browser }
node "应用主机（课程原型）" {
 node "反向代理 / TLS" as Proxy
 artifact "模块化单体应用" as App
 folder "图片文件（随机文件名、限制类型）" as Files
}
node "数据主机（可与应用同机）" {
 database "PostgreSQL\n事务、约束、索引" as DB
 folder "备份 / 恢复资料" as Backup
}
Browser --> Proxy : HTTPS
Proxy --> App : 内部HTTP
App --> DB : 私有数据库连接
App --> Files : 校验后读写
DB --> Backup : 定期备份（设计方案）
note bottom of App
应用负责鉴权；前端隐藏按钮不是安全边界
原型不接真实支付、物流或消息服务
end note
'''),
'04_用例图':('行为性 / 用例图','''left to right direction
actor "普通用户" as User
actor "买家（业务角色）" as Buyer
actor "卖家（业务角色）" as Seller
actor "管理员" as Admin
User <|-- Buyer
User <|-- Seller
rectangle "校园二手交易系统" {
 usecase "注册 / 登录" as Login
 usecase "多条件查询 / 分页" as Search
 usecase "预约 / 确认前取消" as Book
 usecase "确认后申请 / 撤回取消" as Cancel
 usecase "分别确认线下交付" as Finish
 usecase "草稿 / 提交审核 / 修改 / 下架" as Edit
 usecase "接受 / 拒绝预约\n处理取消申请" as Handle
 usecase "本人已完成交易统计" as OwnStats
 usecase "审核 / 违规 / 解除限制" as Review
 usecase "账号停用 / 在途关闭" as Disable
 usecase "分类统计 / 日志查询" as Manage
}
User --> Login
User --> Search
Buyer --> Book
Buyer --> Cancel
Buyer --> Finish
Seller --> Finish
Seller --> Edit
Seller --> Handle
Seller --> OwnStats
Admin --> Review
Admin --> Disable
Admin --> Manage
note bottom of User
买卖是同一普通账号在不同业务中的角色
管理员注册仅经初始化或受控邀请
end note
'''),
'05_商品状态图':('行为性 / 状态图','''state "草稿" as Draft
state "待审核" as Reviewing
state "已发布" as Published
state "已预约" as Reserved
state "已确认" as Confirmed
state "已完成" as Completed
state "已下架" as Offline
[*] --> Draft
Draft --> Reviewing : 提交有效信息
Reviewing --> Draft : 驳回并附原因
Reviewing --> Published : 审核通过
Published --> Reviewing : 修改并重审 / version更新
Published --> Reserved : 当前版本预约成功
Reserved --> Published : 买家取消 / 卖家拒绝
Reserved --> Confirmed : 卖家接受
Confirmed --> Confirmed : 单方交付确认
Confirmed --> Confirmed : 申请取消 / 暂停完成
Confirmed --> Confirmed : 拒绝或撤回取消\n清空标记、epoch递增
Confirmed --> Published : 卖家同意取消
Confirmed --> Completed : 双方确认且无待处理取消
Published --> Offline : 主动下架或违规
Reserved --> Offline : 违规 / 关闭预约
Confirmed --> Offline : 违规 / 关闭预约
Reviewing --> Offline : 违规处置
Draft --> Offline : 违规处置
Offline --> Draft : 主动恢复或违规解限后重提
Completed --> Completed : 违规只加限制 / 不改完成事实
note bottom of Completed
禁止再预约、回草稿、普通撤销、管理员代确认
违规与完成竞争：以先成功生效的事务为准
end note
note left of Confirmed
取消待处理是预约子状态，不新增商品主状态
停用账号在途由管理员有因关闭
不受违规限制的商品才允许恢复发布
end note
'''),
'06_取消活动图':('行为性 / 活动图','''start
:鉴权、核对本人预约;
:开启事务，按商品->预约顺序加锁;
if (仍待卖家处理?) then (是)
 :关闭预约，商品恢复发布;
else (否)
 if (已确认且未完成?) then (是)
  :保存取消申请；预约置CANCEL_PENDING;
  :提交并记录成功审计;
  :等待卖家处理或买家撤回;
  :再次鉴权、加锁并核对申请仍待处理;
  if (卖家同意?) then (是)
   :关闭预约，释放商品占用;
  else (拒绝或买家撤回)
   :恢复CONFIRMED；清空双方标记;
   :confirmationEpoch递增;
  endif
 else (非法状态)
  :回滚，不修改数据；返回状态冲突;
  :另记失败日志;
  stop
 endif
endif
:原子保存商品、预约、申请与成功日志;
:提交事务；返回当前结果;
note right
待处理取消期间不允许完成
任一步数据库异常：整体回滚后记录失败
重复请求根据requestKey返回已保存结果
end note
stop
'''),
'07_预约时序图':('交互性 / 时序图','''actor 买家 as Buyer
boundary 商品详情页 as UI
control ReservationService as S
database PostgreSQL as DB
control AuditService as A
Buyer -> UI : 点击预约
UI -> S : reserve(productId, seenVersion, requestKey)
S -> S : 身份、停用状态、资源权限校验
S -> DB : BEGIN；锁定商品行 FOR UPDATE
S -> DB : 查询(actorId, requestKey)回执
alt 回执已存在且请求摘要相同
 DB --> S : 原处理结果
 S -> DB : COMMIT
 S --> UI : 返回原结果（不再建预约）
else 新请求
 S -> DB : 核对版本、已发布、非本人、无限制
 alt 版本/状态不符
  S -> DB : ROLLBACK
  S -> A : 独立记录失败原因
  S --> UI : 409 提示刷新，不创建预约
 else 可预约
  S -> DB : INSERT预约（价格快照）
  S -> DB : 更新商品为已预约、保存回执与成功日志
  note over S,DB
   部分唯一索引阻止同商品多条有效预约
   中间异常全部回滚；回执与业务结果同事务
  end note
  S -> DB : COMMIT
  S --> UI : 201 返回预约及当前状态
 end
end
UI --> Buyer : 成功页或明确异常下一步
''')}
for name,(kind,body) in diagrams.items():
 (R/'UML'/f'{name}.puml').write_text('@startuml\ntitle '+name[3:]+'（'+kind+'）\n'+common+body+'\n@enduml\n',encoding='utf-8')

SCREENS=[
('01_登录注册','身份与权限','登录校园闲置市场','REQ-002 / 003 / 028 / 029',[
 ('登录账号','普通账号同时买卖，管理员使用受控账号'),('账号','student_demo'),('密码','••••••••••••'),('登录','进入当前账号有权限的工作区'),('注册普通账号','不显示管理员角色选项'),('错误状态','账号或密码不正确；保留账号，清空密码')]),
('02_市场查询','商品查询','让闲置，遇见下一位主人','REQ-011 / 012 / 032',[
 ('关键词','线性代数教材'),('组合筛选','教材 · ¥20-¥200 · 已发布 · 最新发布'),('线性代数教材','¥35.00  / 九成新 / 商品 P-1024'),('折叠台灯','¥48.00  / 生活用品 / 商品 P-1025'),('分页','共 41 条，每页 20 条  |  1  2  3'),('空与错误状态','无结果可清除筛选；最低价大于最高价时就地提示')]),
('03_商品详情','预约发起','线性代数教材 · 九成新','REQ-013 / 014 / 022 / 033',[
 ('商品 P-1024','当前版本 v8 / 已发布 / 卖家 S-208'),('¥35.00','价格将保存为本次预约快照'),('物品描述','无缺页，少量铅笔标记；线下自行交付'),('预约此商品','提交后等待卖家确认，不涉及在线付款'),('边界提示','自己的商品仅显示管理入口；已占用商品不可预约'),('版本已变化弹窗','本次未创建预约。请刷新查看当前价格，再确认提交。')]),
('04_商品发布','草稿与审核','发布一件闲置物品','REQ-005 / 006 / 007 / 008',[
 ('商品名称 *','线性代数教材'),('分类 * / 价格 *','教材 / ¥35.00'),('图片 * / 描述 *','添加图片；说明成色与使用情况'),('保存草稿','允许继续补齐信息，不直接公开'),('提交审核','管理员通过后公开；已发布修改需重新审核'),('校验提示','金额范围 (0, 99999.99]，最多两位小数；缺项就地提示')]),
('05_我的商品','商品管理','我的发布','REQ-008 / 009 / 010 / 020',[
 ('状态筛选','全部 · 草稿 · 待审核 · 已发布 · 已预约 · 已确认 · 已完成 · 已下架'),('P-1024 线性代数教材','已预约 · ¥35.00 | 查看预约；改价和普通下架禁用'),('P-1025 折叠台灯','已发布 · ¥48.00 | 编辑重审 / 下架'),('P-1026 骑行头盔','已下架（违规限制）| 原因可见，等待管理员解除'),('恢复流程','主动下架回草稿重审；违规需先解限，完成不可恢复'),('驳回反馈','商品描述缺失成色说明：修改后重新提交')]),
('06_交易工作台','交易列表','我的购买与出售','REQ-016 / 017 / 018 / 019',[
 ('业务视角','我买到的  |  我卖出的（同一个账号）'),('R-3001 线性代数教材','待卖家确认 · ¥35.00 | 买家取消；卖家接受 / 拒绝'),('R-3002 折叠台灯','已确认 · ¥48.00 | 查看详情 / 申请取消'),('R-3003 书架','取消申请待处理 | 先处理申请，不允许完成'),('历史交易','已完成 / 已取消 / 已拒绝记录保留，不能删除'),('重复提交反馈','正在处理；重试返回原结果，不额外创建预约')]),
('07_交易确认','双方确认','等待双方确认线下交付','REQ-018 / 019 / 025',[
 ('交易 R-3002','商品P-1025 / 预约快照 ¥48.00 / 轮次 epoch=2'),('买家确认','已确认 · 2026-11-03 14:20（示例）'),('卖家确认','待确认 · 只有另一方确认后才完成'),('确认已交付','只代表本人；不可替另一方确认'),('申请取消','单方已确认仍可申请；申请处理中暂停完成'),('完成后状态','显示一次完成时间；重复确认不改时间，不再次计入统计')]),
('08_取消处理','协商取消','处理取消申请','REQ-018 / 020',[
 ('当前申请','C-5001 / R-3002 / 买家申请：临时不再需要'),('卖家同意取消','关闭本次交易、恢复发布；保留申请历史'),('卖家拒绝取消','恢复已确认，并清空双方原交付确认标记'),('买家撤回申请','仅卖家尚未处理时允许；同样清空旧确认'),('确认轮次更新','epoch 2 → 3，旧页面确认请求必须失败'),('操作冲突','该申请已处理，请刷新；不得重复释放或覆盖结果')]),
('09_审核治理','商品审核与违规','管理员 · 审核与治理','REQ-007 / 010 / 034',[
 ('审核队列','待审核商品：P-1031 / 教材 / ¥20.00 / v3'),('审核通过','仅处理当前待审核版本；通过后发布'),('审核驳回','必填原因：缺少实拍图片；退回草稿'),('违规处置','必填原因，强制下架并关闭未完成预约'),('解除限制','仅恢复重审资格，不直接重新发布'),('已完成交易','违规只加展示限制，不改为取消，不删除历史')]),
('10_账号治理','账号与在途处置','管理员 · 停用账号','REQ-002 / 004 / 028',[
 ('账号列表','U-208 / 正常 / 普通用户 / 在途交易2条'),('停用账号','阻止新发布与新预约；历史不删除'),('在途处理','R-3010 待确认；R-3011 已确认'),('有因关闭','必须填写原因；释放占用，不替用户完成'),('管理员邀请','通过受控邀请创建管理员，普通注册无此权限'),('权限边界','普通用户访问本页显示403，服务端再次校验')]),
('11_统计汇总','统计分析','交易与商品概览','REQ-023 / 024',[
 ('卖家视图','北京时间 2026-11-01 00:00 至 11-04 00:00（右端不含）'),('已完成交易','3 笔（示例） / ¥128.00 / 使用预约快照合计'),('管理员视图','分类 + 状态筛选；默认当前全部状态商品'),('分类数量示例','教材 18 / 数码 12 / 生活 11；每商品只计一次'),('口径说明','未完成、取消不计卖家收入；不是实际付款收入'),('空与边界','无交易显示0；起点含、终点不含；不提供Excel导出')]),
('12_操作日志','审计查询','管理员 · 操作日志','REQ-026 / 027 / 036',[
 ('组合筛选','时间范围 / 操作者 / 操作类型'),('日志 A-7001','U-208 · 预约 · P-1024 · 成功 · 已发布→已预约'),('日志 A-7002','U-209 · 预约 · P-1024 · 失败 · STATE_CONFLICT'),('详情','请求编号、对象、前后状态、结果、时间和必要原因'),('只读与保留','包括管理员均无改删入口，至少保存到课程结束'),('安全提示','不记录密码、凭据和不必要个人隐私')])]

# draw.io 原生 XML：每屏一页，控件均为可编辑图形，PNG由官方工具导出。
mx=E.Element('mxfile',host='app.diagrams.net',agent='Codex authoring',version='32.6.1')
def screen(name,module,title,refs,rows):
 d=E.SubElement(mx,'diagram',name=name,id=name)
 model=E.SubElement(d,'mxGraphModel',dx='1400',dy='1000',grid='1',gridSize='10',page='1',pageWidth='1440',pageHeight='1000',background='#F5F7FB')
 rt=E.SubElement(model,'root');E.SubElement(rt,'mxCell',id='0');E.SubElement(rt,'mxCell',id='1',parent='0');idx=1
 def box(x,y,w,h,text='',fill='#FFFFFF',stroke='none',size=18,color='#19334B',bold=False,rounded=True):
  nonlocal idx
  idx+=1
  cell=E.SubElement(rt,'mxCell',id=str(idx),value=text,vertex='1',parent='1',style=f'rounded={int(rounded)};whiteSpace=wrap;html=0;fillColor={fill};strokeColor={stroke};fontFamily=Microsoft YaHei;fontSize={size};fontColor={color};fontStyle={int(bold)};align=left;verticalAlign=middle;spacingLeft=18;spacingRight=15;arcSize=12;')
  E.SubElement(cell,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),attrib={'as':'geometry'})
 box(0,0,1440,1000,'','#F5F7FB',rounded=False)
 box(0,0,240,1000,'','#142E43',rounded=False)
 box(20,25,210,55,'拾光 · 校园闲置','#142E43',size=23,color='#FFFFFF',bold=True)
 for i,t in enumerate(['市场发现','发布与管理','交易工作台','统计汇总','管理后台','操作日志']):box(20,125+i*70,200,50,t,'#286B70' if module in t else '#142E43',size=18,color='#FFFFFF')
 box(265,20,1145,60,'校园二手交易系统    /    '+module+'                                      示例账号 · 已登录','#FFFFFF',size=17)
 box(275,110,1125,55,title,'#F5F7FB',size=30,bold=True)
 box(275,170,1125,36,refs+'   |   设计稿 · 示例数据，不含真实交易','#F5F7FB',size=14,color='#687D8B')
 for i,(label,detail) in enumerate(rows):
  y=230+i*110
  box(275,y,1125,94,'','#FFFFFF',stroke='#DFE7ED')
  box(285,y+8,1080,30,label,'#FFFFFF',size=19,color='#16766F',bold=True)
  box(285,y+40,1080,42,detail,'#FFFFFF',size=17)
 box(275,920,1125,42,'设计重点：服务端鉴权 + 状态/版本校验 + 事务一致性；禁用按钮附原因，失败给出下一步。','#E8F3F0',size=15,color='#28665F')
for s in SCREENS:screen(*s)
E.ElementTree(mx).write(R/'UI界面'/'校园二手交易UI.drawio',encoding='utf-8',xml_declaration=True)
(W/'screens.json').write_text(json.dumps(SCREENS,ensure_ascii=False,indent=2),encoding='utf-8')
(W/'diagrams.json').write_text(json.dumps({k:v[0] for k,v in diagrams.items()},ensure_ascii=False),encoding='utf-8')
print('已生成7种PlantUML源文件、12页原生draw.io UI设计。')
