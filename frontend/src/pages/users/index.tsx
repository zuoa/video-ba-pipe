import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Form, Input, message, Modal, Select, Space, Switch, Table } from 'antd';
import { useEffect, useState } from 'react';
import { getUsers, createUser, updateUser, deleteUser } from '@/services/api';
import './index.css';

interface User {
  id: number;
  username: string;
  role: string;
  enabled: boolean;
  created_at: string;
  last_login: string | null;
}

export default function Users() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [form] = Form.useForm();

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      if (data.success) {
        setUsers(data.users);
      }
    } catch (error) {
      message.error('获取用户列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleAdd = () => {
    setEditingUser(null);
    form.resetFields();
    setModalVisible(true);
  };

  const handleEdit = (user: User) => {
    setEditingUser(user);
    form.setFieldsValue({ username: user.username, role: user.role, enabled: user.enabled });
    setModalVisible(true);
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除该用户吗？',
      onOk: async () => {
        try {
          const data = await deleteUser(id);
          if (data.success) {
            message.success('删除成功');
            fetchUsers();
          } else {
            message.error(data.error);
          }
        } catch (error) {
          message.error('删除失败');
        }
      },
    });
  };

  const handleSubmit = async (values: any) => {
    try {
      const data = editingUser 
        ? await updateUser(editingUser.id, values)
        : await createUser(values);

      if (data.success) {
        message.success(editingUser ? '更新成功' : '创建成功');
        setModalVisible(false);
        fetchUsers();
      } else {
        message.error(data.error);
      }
    } catch (error) {
      message.error(editingUser ? '更新失败' : '创建失败');
    }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 80 },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '角色',
      dataIndex: 'role',
      render: (role: string) => (role === 'admin' ? '管理员' : '普通用户'),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      render: (enabled: boolean) => (
        <Switch checked={enabled} disabled size="small" />
      ),
    },
    {
      title: '最后登录',
      dataIndex: 'last_login',
      render: (time: string | null) => time ? new Date(time).toLocaleString() : '-',
    },
    {
      title: '操作',
      width: 150,
      render: (_: any, record: User) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Button
            type="link"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.id)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="users-page">
      <div className="page-header">
        <h1>用户管理</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          新增用户
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={users}
        rowKey="id"
        loading={loading}
      />

      <Modal
        title={editingUser ? '编辑用户' : '新增用户'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        footer={null}
      >
        <Form form={form} onFinish={handleSubmit} layout="vertical">
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input disabled={!!editingUser} />
          </Form.Item>
          {!editingUser && (
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password />
            </Form.Item>
          )}
          {editingUser && (
            <Form.Item name="password" label="新密码（留空不修改）">
              <Input.Password />
            </Form.Item>
          )}
          <Form.Item
            name="role"
            label="角色"
            rules={[{ required: true }]}
            initialValue="user"
          >
            <Select>
              <Select.Option value="user">普通用户</Select.Option>
              <Select.Option value="admin">管理员</Select.Option>
            </Select>
          </Form.Item>
          {editingUser && (
            <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}>
              <Switch />
            </Form.Item>
          )}
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                提交
              </Button>
              <Button onClick={() => setModalVisible(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

