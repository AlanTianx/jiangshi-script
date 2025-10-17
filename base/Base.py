import pytesseract
import win32gui
import win32con
import win32com.client
import pyautogui
import cv2
import numpy as np
from PIL import ImageGrab
import time
import os

class WindowAutomation:
    def __init__(self):
        self.shell = win32com.client.Dispatch("WScript.Shell")

    def find_window(self, window_title=None):
        #查找窗口并返回窗口句柄
        def callback(hwnd, extra):
            if window_title and window_title in win32gui.GetWindowText(hwnd):
                extra.append(hwnd)
        windows = []
        win32gui.EnumWindows(callback, windows)
        return windows[0] if windows else None

    def activate_window(self, hwnd):
        #激活窗口并使其前置
        if win32gui.IsIconic(hwnd):  # 如果窗口最小化
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)  # 等待窗口激活

    def get_window_rect(self, hwnd):
        #获取窗口的位置和大小
        rect = win32gui.GetWindowRect(hwnd)
        return {
            'left': rect[0],
            'top': rect[1],
            'right': rect[2],
            'bottom': rect[3],
            'width': rect[2] - rect[0],
            'height': rect[3] - rect[1]
        }
    
    def set_window_size(self, hwnd, width, height):
        try:
            # 获取当前窗口位置和大小
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            current_width = right - left
            current_height = bottom - top
            
            # 计算新的窗口位置（保持左上角不变）
            new_right = left + width
            new_bottom = top + height
            
            # 设置窗口大小
            win32gui.MoveWindow(hwnd, left, top, width, height, True)
            
            # 验证是否设置成功
            new_left, new_top, new_right, new_bottom = win32gui.GetWindowRect(hwnd)
            new_width = new_right - new_left
            new_height = new_bottom - new_top
            
            return new_width == width and new_height == height
        except Exception as e:
            print(f"设置窗口大小出错: {e}")
            return False

    def capture_window(self, hwnd=None):
        #截取窗口内容
        if hwnd:
            rect = self.get_window_rect(hwnd)
            screenshot = ImageGrab.grab(bbox=(rect['left'], rect['top'],
                                           rect['right'], rect['bottom']))
        else:
            screenshot = ImageGrab.grab()  # 截取全屏
        return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def find_image_in_window(self, window_image, template_path, threshold=0.8):
        #在窗口图像中查找模板图像
        template = cv2.imread(template_path, 0)
        if template is None:
            raise FileNotFoundError(f"模板图像不存在: {template_path}")

        window_gray = cv2.cvtColor(window_image, cv2.COLOR_BGR2GRAY)
        w, h = template.shape[::-1]

        result = cv2.matchTemplate(window_gray, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)


        if max_val >= threshold:
            return {
                'x': max_loc[0],
                'y': max_loc[1],
                'width': w,
                'height': h,
                'confidence': max_val
            }
        return None
    def find_text_in_window(self, window_image, target_text, language='eng', threshold=0.8):
        
        #在窗口图像中查找指定文本
        #:param window_image: 窗口图像 (OpenCV格式)
        #:param target_text: 要查找的目标文本
        #:param language: 文本语言，默认为英文 ('eng')
        #:param threshold: 匹配阈值，0-1之间
        #:return: 包含文本的区域位置信息，如果未找到则返回None
        
        # 转换为灰度图
        gray = cv2.cvtColor(window_image, cv2.COLOR_BGR2GRAY)
        
        # 使用Tesseract进行OCR识别
        data = pytesseract.image_to_data(gray, lang=language, output_type=pytesseract.Output.DICT)
        
        # 查找匹配的文本
        for i in range(len(data['text'])):
            text = data['text'][i].strip().lower()
            if not text:
                continue
                
            # 计算相似度
            similarity = self._calculate_text_similarity(text, target_text.lower())
            
            # 如果相似度超过阈值，返回位置信息
            if similarity >= threshold:
                x = data['left'][i]
                y = data['top'][i]
                w = data['width'][i]
                h = data['height'][i]
                
                return {
                    'x': x,
                    'y': y,
                    'width': w,
                    'height': h,
                    'text': text,
                    'confidence': similarity
                }
                
        return None

    def _calculate_text_similarity(self, text1, text2):
        #"""计算两个文本的相似度（简化版）"""
        # 这里使用简单的公共子序列长度比例作为相似度
        # 实际应用中可以使用更复杂的算法，如Levenshtein距离等
        common_length = 0
        i = j = 0
        
        while i < len(text1) and j < len(text2):
            if text1[i] == text2[j]:
                common_length += 1
                i += 1
                j += 1
            else:
                # 尝试不同的对齐方式
                if len(text1) - i > len(text2) - j:
                    i += 1
                else:
                    j += 1
                    
        return common_length / max(len(text1), len(text2)) if max(len(text1), len(text2)) > 0 else 0
    def find_button_in_region(self, window_image, region, button_image_path, threshold=0.8):
        #在指定区域内查找按钮图像
        # 参数:
        #window_image: 窗口的完整截图 (OpenCV格式)
        #region: 搜索区域，格式为 {'x': x, 'y': y, 'width': w, 'height': h}
        #button_image_path: 按钮图像的文件路径
        #threshold: 匹配阈值，范围0-1，值越高匹配越严格
        
        #返回:
        # 按钮的位置信息 (字典)，包含坐标、大小和置信度
        # 若未找到匹配项，返回None
        
        # 读取按钮模板图像
        button_template = cv2.imread(button_image_path, 0)
        if button_template is None:
            raise FileNotFoundError(f"按钮模板图像不存在: {button_image_path}")
        
        # 从完整窗口图像中提取搜索区域
        region_image = window_image[
            region['y']:region['y']+region['height'],
            region['x']:region['x']+region['width']
        ]
        
        # 转换为灰度图进行匹配
        region_gray = cv2.cvtColor(region_image, cv2.COLOR_BGR2GRAY)
        button_gray = button_template
        
        # 执行模板匹配
        result = cv2.matchTemplate(region_gray, button_gray, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        # 如果匹配度达到阈值，返回按钮在原始窗口中的位置
        if max_val >= threshold:
            button_x = region['x'] + max_loc[0]
            button_y = region['y'] + max_loc[1]
            button_w, button_h = button_template.shape[::-1]
            
            return {
                'x': button_x,
                'y': button_y,
                'width': button_w,
                'height': button_h,
                'confidence': max_val,
                'center_x': button_x + button_w // 2,  # 按钮中心点x坐标
                'center_y': button_y + button_h // 2   # 按钮中心点y坐标
            }
        
        return None

    def click_in_window(self, hwnd, x, y, duration=0.01):
        #在窗口内指定位置点击
        window_rect = self.get_window_rect(hwnd)
        screen_x = window_rect['left'] + x
        screen_y = window_rect['top'] + y

        pyautogui.moveTo(screen_x, screen_y, duration=duration)
        pyautogui.click()