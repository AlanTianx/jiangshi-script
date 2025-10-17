import datetime
import threading
import logging
import time
import multiprocessing
from base.Base import WindowAutomation

# 查找主动邀请
def me_invite(auto : WindowAutomation, target_window):
    print("开始主动邀请")
    while True:
        # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        
        # 3. 在窗口中查找按钮
        button_location = auto.find_image_in_window(
            window_image, 
            template_path="image/me-invite.png",
            threshold=0.8
        )
        if button_location:
            # 4. 计算按钮中心并点击
            center_x = button_location['x'] + button_location['width'] // 2
            center_y = button_location['y'] + button_location['height'] // 2
            
            auto.click_in_window(target_window, center_x, center_y)
            print("打开邀请界面")
            logging.info("打开邀请界面")
            time.sleep(0.5)
            if friend(auto, target_window):
                all_invite(auto, target_window)
                time.sleep(31)
                
        else:
            time.sleep(0.5)
# 选择好友
def friend(auto : WindowAutomation, target_window):
    # 2. 截取窗口内容
    window_image = auto.capture_window(target_window)
    
    # 3. 在窗口中查找按钮
    button_location = auto.find_image_in_window(
        window_image, 
        template_path="image/friend.png",
        threshold=0.8
    )
    if button_location:
        # 4. 计算按钮中心并点击
        center_x = button_location['x'] + button_location['width'] // 2
        center_y = button_location['y'] + button_location['height'] // 2
        
        auto.click_in_window(target_window, center_x, center_y)
        print("点击了好友")
        logging.info("打开 好友 邀请界面")
        return True
    else:
        return False

# 点击一键邀请
def all_invite(auto : WindowAutomation, target_window):
    while True:
        # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        
        # 3. 在窗口中查找按钮
        button_location = auto.find_image_in_window(
            window_image, 
            template_path="image/all-invite.png",
            threshold=0.8
        )
        if button_location:
            # 4. 计算按钮中心并点击
            center_x = button_location['x'] + button_location['width'] // 2
            center_y = button_location['y'] + button_location['height'] // 2
            
            auto.click_in_window(target_window, center_x, center_y)
            print("一键邀请了好友")
            logging.info("一键邀请了好友")
        time.sleep(10)

# 点击关闭一键邀请界面
def close(auto : WindowAutomation, target_window):
    # 2. 截取窗口内容
    window_image = auto.capture_window(target_window)
    
    # 3. 在窗口中查找按钮
    button_location = auto.find_image_in_window(
        window_image, 
        template_path="image/all-invite.png",
        threshold=0.8
    )
    if button_location:
        # 4. 计算按钮中心并点击
        center_x = button_location['x'] + button_location['width'] // 2
        center_y = button_location['y'] + button_location['height'] // 2
        
        auto.click_in_window(target_window, center_x, center_y)
        print("一键邀请了好友")
        logging.info("一键邀请了好友")

# 查找邀请
def invite(auto : WindowAutomation, target_window):
    print("开始查找 邀请")
    while True:
        # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        
        # 3. 在窗口中查找按钮
        button_location = auto.find_image_in_window(
            window_image, 
            template_path="image/invite.png",  # 替换为实际按钮截图
            threshold=0.8
        )
        if button_location:
            print("找到邀请了------自动点击按钮打开邀请列表-----")
            # 4. 计算按钮中心并点击
            center_x = button_location['x'] + button_location['width'] // 2
            center_y = button_location['y'] + button_location['height'] // 2
            
            auto.click_in_window(target_window, center_x, center_y)
            print("已点击按钮-打开了邀请列表")
            logging.info("打开了邀请列表-等待找环任务")
        else:
            time.sleep(0.5)

# 找到环 并点击同意
def findHuan(auto : WindowAutomation, target_window):
    print("开始查找 环任务")
    while True:
    # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        res = auto.find_image_in_window(
            window_image, 
            template_path='image/huan.png',  # 替换为实际按钮截图
            threshold=0.8
        )
        if res:
            print(res, '找到的环了---- 开始找同意按钮')
            # 扩展位置和高度
            res['y'] = res['y'] - 55
            res['height'] = res['height'] + 55
            res['width'] = res['width'] + 160
            
            res1 = auto.find_button_in_region(window_image, res, 'image/ok.png', 0.8)
            if res1:
                print(res1, '找到同意按钮----开始准备点击')
                auto.click_in_window(target_window,res1['center_x'], res1['center_y'])
                print('点击成功 进入组队界面')
                logging.info("成功进入组队界面-等待开始")
                time.sleep(5)
            else:
                time.sleep(0.5)
        else:
            time.sleep(0.5)

# 结束后找到返回按钮 点击返回 10秒执行一次
def findEnd(auto : WindowAutomation, target_window):
    times = 0
    print("开始查找 战斗结束按钮")
    #auto.activate_window(target_window)
    # 2. 截取窗口内容
    while True:
        window_image = auto.capture_window(target_window)
        res = auto.find_image_in_window(
            window_image, 
            template_path='image/return.png',  # 替换为实际按钮截图
            threshold=0.8
        )
        if res:
            print(res, '找到返回按钮了')
            center_x = res['x'] + res['width'] // 2
            center_y = res['y'] + res['height'] // 2
        
            auto.click_in_window(target_window,center_x, center_y)
            
            times +=1
            logging.info(f'战斗结束：点击返回按钮 现在已经打环 {times} 次')
            print(f'战斗结束：点击返回按钮 现在已经打环 {times} 次')
            time.sleep(180)
        else:
            time.sleep(10)
            print(f'现在已经打环 {times} 次')

# 打环
def huan(auto : WindowAutomation, target_window):
    taskInvite = threading.Thread(target=invite, args=(auto, target_window),daemon=True)
    taskFindHuan = threading.Thread(target=findHuan, args=(auto, target_window),daemon=True)
    taskFindEnd = threading.Thread(target=findEnd, args=(auto, target_window),daemon=True)
    print("开始任务")
    logging.info("开始任务")

    taskInvite.start()
    taskFindHuan.start()
    taskFindEnd.start()
    
    while True:
        time.sleep(60)


# 开始游戏
def open_game(auto : WindowAutomation, target_window):
    while True:
        # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        
        # 3. 在窗口中查找按钮
        button_location = auto.find_image_in_window(
            window_image, 
            template_path="image/open-game.png",  # 替换为实际按钮截图
            threshold=0.8
        )
        if button_location:
            # 4. 计算按钮中心并点击
            center_x = button_location['x'] + button_location['width'] // 2
            center_y = button_location['y'] + button_location['height'] // 2
            
            auto.click_in_window(target_window, center_x, center_y)
        else:
            time.sleep(1)

# 选择JN-之选中间的
def choseJN(auto : WindowAutomation, target_window, q):
    i=0
    while True:
        # 2. 截取窗口内容
        window_image = auto.capture_window(target_window)
        
        # 3. 在窗口中查找按钮
        button_location = auto.find_image_in_window(
            window_image, 
            template_path="image/shuaxin.png",  # 替换为实际按钮截图
            threshold=0.8
        )
   
        if button_location:
            # 扩展位置和高度
            button_location['y'] = button_location['y'] - 280
            button_location['height'] = button_location['height'] + 280

            # 4. 计算按钮中心并点击
            center_x = button_location['x'] + button_location['width'] // 2
            center_y = button_location['y'] + button_location['height'] // 2
            
            auto.click_in_window(target_window, center_x, center_y)
            i = i+1
            if i >=4:
                q.put("tuichu")
                i=0
        else:
            time.sleep(3)

# 选择JN-之选中间的
def tuiyou(auto : WindowAutomation, target_window):
    print("5级退出游戏")

    auto.click_in_window(target_window,38, 67)
    time.sleep(0.5)

    window_image = auto.capture_window(target_window)
    res = auto.find_image_in_window(
        window_image, 
        template_path='image/tuichu.png',  # 替换为实际按钮截图
        threshold=0.8
    )
    if res:
        center_x = res['x'] + res['width'] // 2
        center_y = res['y'] + res['height'] // 2
    
        auto.click_in_window(target_window,center_x, center_y)

        time.sleep(0.5)

        window_image1 = auto.capture_window(target_window)
        res2 = auto.find_image_in_window(
            window_image1, 
            template_path='image/return.png',  # 替换为实际按钮截图
            threshold=0.8
        )
        if res2:
            center_x1 = res2['x'] + res2['width'] // 2
            center_y2 = res2['y'] + res2['height'] // 2
            auto.click_in_window(target_window,center_x1, center_y2)
    else:
        tuiyou(auto,target_window)


# 扫荡
def saodang(auto : WindowAutomation, target_window):

    q = multiprocessing.Queue()

    taskOpenGame = threading.Thread(target=open_game, args=(auto, target_window),daemon=True)
    taskChoseJN = threading.Thread(target=choseJN, args=(auto, target_window,q),daemon=True)
    
 
    print("开始任务")
    logging.info("开始任务")

    taskOpenGame.start()
    taskChoseJN.start()
    i = 0

    while True:
        msg = q.get()
        if msg == "tuichu":
            tuiyou(auto,target_window)
            i = i+1
            print(f"退出游戏成功,扫荡结束 当前扫荡成功次数 {i}")
        time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,  # 设置日志级别（DEBUG < INFO < WARNING < ERROR < CRITICAL）
        format='%(asctime)s - %(levelname)s - %(message)s',  # 日志格式
        filename='app.log',  # 输出到文件（可选，若不指定则输出到控制台）
        encoding='utf-8'
    )
    # 初始化自动化工具
    auto = WindowAutomation()
    
    # 1. 查找并激活目标窗口
    target_window = auto.find_window(window_title="向僵尸开炮")  # 替换为实际窗口标题
    if not target_window:
        print("未找到目标窗口")
        exit()
    
    auto.activate_window(target_window)
    auto.set_window_size(target_window, 418,803)

    print("请选择要执行的程序 A-打环、B-关卡扫荡")

    uiput = input("请选择 （A/B）: ").strip()
    if uiput.upper() == 'A':
        huan(auto,target_window)
        while True:
            time.sleep(60)
    elif uiput.upper() == 'B':
        saodang(auto,target_window)
        while True:
            time.sleep(60)
    else:
        print("信球，你干啥？") 
    
    
        
    
    
    
