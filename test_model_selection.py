"""
测试算法模型选择功能
"""
import os
import sys
import json

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.database_models import Algorithm, MLModel, db

def test_model_ids_field():
    """测试 model_ids 字段是否存在"""
    print("=" * 60)
    print("测试 1: 检查 model_ids 字段")
    print("=" * 60)
    
    try:
        # 查询算法表结构
        cursor = db.execute_sql("PRAGMA table_info(algorithm)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        if 'model_ids' in columns:
            print("✓ model_ids 字段存在")
            print(f"  类型: {columns['model_ids']}")
        else:
            print("✗ model_ids 字段不存在")
            return False
        
        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False

def test_algorithm_property():
    """测试 Algorithm.model_id_list 属性"""
    print("\n" + "=" * 60)
    print("测试 2: 检查 model_id_list 属性")
    print("=" * 60)
    
    try:
        # 创建测试算法
        test_algo = Algorithm(
            name="test_model_selection",
            model_ids='[1, 2, 3]',
            model_json='{}',
            interval_seconds=1.0
        )
        
        # 测试属性
        model_list = test_algo.model_id_list
        print(f"✓ model_id_list 属性正常")
        print(f"  返回值: {model_list}")
        print(f"  类型: {type(model_list)}")
        
        if model_list == [1, 2, 3]:
            print("✓ 解析结果正确")
        else:
            print(f"✗ 解析结果错误: 期望 [1, 2, 3], 实际 {model_list}")
            return False
        
        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_model_usage_methods():
    """测试模型使用计数方法"""
    print("\n" + "=" * 60)
    print("测试 3: 检查模型使用计数方法")
    print("=" * 60)
    
    try:
        # 检查 MLModel 是否有相关方法
        if hasattr(MLModel, 'increment_usage'):
            print("✓ MLModel.increment_usage() 方法存在")
        else:
            print("✗ MLModel.increment_usage() 方法不存在")
            return False
        
        if hasattr(MLModel, 'decrement_usage'):
            print("✓ MLModel.decrement_usage() 方法存在")
        else:
            print("✗ MLModel.decrement_usage() 方法不存在")
            return False
        
        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False

def test_existing_algorithms():
    """测试现有算法数据"""
    print("\n" + "=" * 60)
    print("测试 4: 检查现有算法数据")
    print("=" * 60)
    
    try:
        algorithms = Algorithm.select().limit(5)
        count = algorithms.count()
        
        if count == 0:
            print("  数据库中暂无算法")
            return True
        
        print(f"  找到 {count} 个算法 (显示前5个)")
        
        for algo in algorithms:
            model_ids = getattr(algo, 'model_ids', '[]')
            print(f"\n  算法: {algo.name}")
            print(f"    ID: {algo.id}")
            print(f"    model_ids: {model_ids}")
            print(f"    model_json: {algo.model_json[:50]}...")
            
            # 测试属性
            try:
                model_list = algo.model_id_list
                print(f"    解析后的ID列表: {model_list}")
            except Exception as e:
                print(f"    ✗ 解析失败: {e}")
        
        print("\n✓ 现有算法数据检查完成")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_existing_models():
    """测试现有模型数据"""
    print("\n" + "=" * 60)
    print("测试 5: 检查现有模型数据")
    print("=" * 60)
    
    try:
        models = MLModel.select().limit(5)
        count = models.count()
        
        if count == 0:
            print("  数据库中暂无模型")
            print("  建议: 请先在模型管理页面上传模型")
            return True
        
        print(f"  找到 {count} 个模型 (显示前5个)")
        
        for model in models:
            print(f"\n  模型: {model.name}")
            print(f"    ID: {model.id}")
            print(f"    类型: {model.model_type}")
            print(f"    框架: {model.framework}")
            print(f"    路径: {model.file_path}")
            print(f"    使用次数: {model.usage_count}")
        
        print("\n✓ 现有模型数据检查完成")
        return True
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("算法模型选择功能测试")
    print("=" * 60 + "\n")
    
    results = []
    
    # 运行测试
    results.append(("model_ids字段", test_model_ids_field()))
    results.append(("model_id_list属性", test_algorithm_property()))
    results.append(("模型使用计数方法", test_model_usage_methods()))
    results.append(("现有算法数据", test_existing_algorithms()))
    results.append(("现有模型数据", test_existing_models()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    for test_name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{status}  {test_name}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    print(f"\n通过: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 所有测试通过！功能已正常部署。")
        print("\n下一步:")
        print("1. 启动Web服务: python app/web/webapp.py")
        print("2. 访问算法管理页面")
        print("3. 创建或编辑算法，测试模型选择功能")
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查配置。")

if __name__ == '__main__':
    main()

