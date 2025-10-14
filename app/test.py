
from ultralytics import YOLO

model = YOLO("/Users/yujian/Downloads/damoyolo_tinynasL25_S_phone.pt")  # 加载模型

results = model.predict("/Users/yujian/Downloads/7F1746D2-5F8A-4C41-8053-77366F952449.png", save=True, conf=0.5)  # 进行预测并保存结果
print(results)  # 输出结果