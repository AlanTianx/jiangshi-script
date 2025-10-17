import cv2
import numpy as np
import pytesseract
import subprocess
import time
import os
import re
from PIL import Image

class AndroidAutomation:
    def __init__(self, device_id=None):
       
        self.device_id = device_id
        self.adb_command = "adb" if not device_id else f"adb -s {device_id}"
        
        # 检查ADB是否可用
        try:
            subprocess.check_output(f"{self.adb_command} devices", shell=True)
        except Exception as e:
            raise Exception(f"ADB不可用，请确保已安装并配置环境变量: {e}")

    def get_device_info(self):
    
        try:
            output = subprocess.check_output(f"{self.adb_command} shell wm size", shell=True).decode()
            match = re.search(r'Physical size: (\d+)x(\d+)', output)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
                return {
                    'width': width,
                    'height': height,
                    'density': self._get_display_density()
                }
            return None
        except Exception as e:
            print(f"获取设备信息失败: {e}")
            return None

    def _get_display_density(self):
 
        try:
            output = subprocess.check_output(f"{self.adb_command} shell wm density", shell=True).decode()
            match = re.search(r'Physical density: (\d+)', output)
            if match:
                return int(match.group(1))
            return 160  # 默认密度
        except:
            return 160

    def start_app(self, package_name, activity_name=None):
        try:
            if activity_name:
                subprocess.check_output(
                    f"{self.adb_command} shell am start -n {package_name}/{activity_name}", 
                    shell=True
                )
            else:
                subprocess.check_output(
                    f"{self.adb_command} shell monkey -p {package_name} 1", 
                    shell=True
                )
            time.sleep(2)  # 等待应用启动
            return True
        except Exception as e:
            print(f"启动应用失败: {e}")
            return False

    def capture_screen(self, output_path=None):
        try:
            # 使用ADB截图
            temp_file = "screen.png"
            subprocess.check_output(f"{self.adb_command} exec-out screencap -p > {temp_file}", shell=True)
            
            # 读取截图
            img = cv2.imread(temp_file)
            
            # 删除临时文件
            if os.path.exists(temp_file):
                os.remove(temp_file)
            
            if output_path:
                cv2.imwrite(output_path, img)
                return True
            return img
        except Exception as e:
            print(f"截图失败: {e}")
            return None

    def find_image_on_screen(self, template_path, threshold=0.8):
        # 获取屏幕截图
        screen_img = self.capture_screen()
        if screen_img is None:
            return None
            
        # 读取模板
        template = cv2.imread(template_path)
        if template is None:
            raise FileNotFoundError(f"模板图像不存在: {template_path}")
            
        # 执行模板匹配
        result = cv2.matchTemplate(screen_img, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        if max_val >= threshold:
            h, w, _ = template.shape
            return {
                'x': max_loc[0],
                'y': max_loc[1],
                'width': w,
                'height': h,
                'confidence': max_val,
                'center_x': max_loc[0] + w // 2,
                'center_y': max_loc[1] + h // 2
            }
        return None
    def click(self, x, y, duration=0.1):
        try:
            if duration <= 0.1:
                # 普通点击
                subprocess.check_output(
                    f"{self.adb_command} shell input tap {x} {y}",
                    shell=True
                )
            else:
                # 长按
                start = int(time.time() * 1000)
                end = start + int(duration * 1000)
                subprocess.check_output(
                    f"{self.adb_command} shell input swipe {x} {y} {x} {y} {int(duration * 1000)}",
                    shell=True
                )
            time.sleep(0.2)
            return True
        except Exception as e:
            print(f"点击失败: {e}")
            return False

    def swipe(self, x1, y1, x2, y2, duration=0.5):
        try:
            subprocess.check_output(
                f"{self.adb_command} shell input swipe {x1} {y1} {x2} {y2} {int(duration * 1000)}",
                shell=True
            )
            time.sleep(duration + 0.2)
            return True
        except Exception as e:
            print(f"滑动失败: {e}")
            return False

    def input_text(self, text):
        try:
            # 替换特殊字符
            text = text.replace(' ', '%s')
            text = text.replace('"', '\\"')
            
            subprocess.check_output(
                f'{self.adb_command} shell input text "{text}"',
                shell=True
            )
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"输入文本失败: {e}")
            return False

    def press_key(self, key_code):
        try:
            subprocess.check_output(
                f"{self.adb_command} shell input keyevent {key_code}",
                shell=True
            )
            time.sleep(0.2)
            return True
        except Exception as e:
            print(f"按键失败: {e}")
            return False

# 使用示例
if __name__ == "__main__":
    # 初始化自动化对象
    android = AndroidAutomation()
    
    # 获取设备信息
    device_info = android.get_device_info()
    print(f"设备信息: {device_info}")
    
    # 启动应用（以计算器为例）
    # android.start_app("com.android.calculator2")
    
    # 截取屏幕
    # android.capture_screen("screen.png")
    
    # 查找图像并点击
    # image_pos = android.find_image_on_screen("button.png")
    # if image_pos:
    #     android.click(image_pos['center_x'], image_pos['center_y'])
    
    # 查找文本并点击
    # text_pos = android.find_text_on_screen("设置")
    # if text_pos:
    #     android.click(text_pos['center_x'], text_pos['center_y'])
    
    # 按返回键
    # android.press_key(4)
