import { getDateLocale } from '@/i18n/tr';
import { tr, trf } from '@/i18n/tr';
import { DeleteOutlined, EditOutlined, PlusOutlined, TeamOutlined } from '@ant-design/icons';
import { Form, Input, message, Select, Space, Switch, Table } from 'antd';
import Button from '@/components/common/AppButton';
import { useEffect, useState } from 'react';
import { getUsers, createUser, updateUser, deleteUser } from '@/services/api';
import { AppModal, PageHeader, useAppConfirm } from '@/components/common';
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
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();
  const confirmAction = useAppConfirm();

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      if (data.success) {
        setUsers(data.users);
      }
    } catch (error) {
      message.error(tr("获取用户列表失败"));
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
    const user = users.find((item) => item.id === id);
    confirmAction({
      title: tr("删除用户"),
      objectName: user?.username || trf("用户 #__VAR0__", [id]),
      description: tr("删除后，该账号将无法登录系统。"),
      onConfirm: async () => {
        try {
          const data = await deleteUser(id);
          if (data.success) {
            message.success(tr("删除成功"));
            fetchUsers();
          } else {
            message.error(data.error);
          }
        } catch (error) {
          message.error(tr("删除失败"));
        }
      },
    });
  };

  const handleSubmit = async (values: any) => {
    setSubmitting(true);
    try {
      const data = editingUser 
        ? await updateUser(editingUser.id, values)
        : await createUser(values);

      if (data.success) {
        message.success(editingUser ? tr("更新成功") : tr("创建成功"));
        setModalVisible(false);
        fetchUsers();
      } else {
        message.error(data.error);
      }
    } catch (error) {
      message.error(editingUser ? tr("更新失败") : tr("创建失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 80 },
    { title: tr("用户名"), dataIndex: 'username' },
    {
      title: tr("角色"),
      dataIndex: 'role',
      render: (role: string) => (role === 'admin' ? tr("管理员") : tr("普通操作员")),
    },
    {
      title: tr("状态"),
      dataIndex: 'enabled',
      render: (enabled: boolean) => (
        <Switch checked={enabled} disabled size="small" />
      ),
    },
    {
      title: tr("最后登录"),
      dataIndex: 'last_login',
      render: (time: string | null) => time ? new Date(time).toLocaleString(getDateLocale()) : '-',
    },
    {
      title: tr("操作"),
      width: 150,
      render: (_: any, record: User) => (
        <Space>
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            {tr("编辑")}
          </Button>
          <Button
            type="link"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.id)}
          >
            {tr("删除")}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="users-page">
      <PageHeader
        icon={<TeamOutlined />}
        eyebrow="ACCESS CONTROL"
        title={tr("用户管理")}
        subtitle={tr("维护系统账号、权限与启用状态")}
        count={users.length}
        countLabel={tr("位用户")}
        extra={(
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleAdd}
            className="app-primary-button create-btn"
          >
            {tr("新增用户")}
          </Button>
        )}
      />

      <Table
        columns={columns}
        dataSource={users}
        rowKey="id"
        loading={loading}
      />

      <AppModal
        title={editingUser ? tr("编辑用户") : tr("新增用户")}
        description={editingUser ? trf("更新 __VAR0__ 的账号信息", [editingUser.username]) : tr("创建新的系统登录账号")}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
        }}
        footer={null}
        size="sm"
        closable={!submitting}
        keyboard={!submitting}
      >
        <Form form={form} onFinish={handleSubmit} layout="vertical">
          <Form.Item
            name="username"
            label={tr("用户名")}
            rules={[{ required: true, message: tr("请输入用户名") }]}
          >
            <Input disabled={!!editingUser} />
          </Form.Item>
          {!editingUser && (
            <Form.Item
              name="password"
              label={tr("密码")}
              rules={[{ required: true, message: tr("请输入密码") }]}
            >
              <Input.Password />
            </Form.Item>
          )}
          {editingUser && (
            <Form.Item name="password" label={tr("新密码（留空不修改）")}>
              <Input.Password />
            </Form.Item>
          )}
          <Form.Item
            name="role"
            label={tr("角色")}
            rules={[{ required: true }]}
            initialValue="user"
          >
            <Select>
              <Select.Option value="user">{tr("普通操作员")}</Select.Option>
              <Select.Option value="admin">{tr("管理员")}</Select.Option>
            </Select>
          </Form.Item>
          {editingUser && (
            <Form.Item name="enabled" label={tr("启用")} valuePropName="checked" initialValue={true}>
              <Switch />
            </Form.Item>
          )}
          <Form.Item className="app-form-actions">
            <Space>
              <Button
                disabled={submitting}
                onClick={() => {
                  setModalVisible(false);
                  form.resetFields();
                }}
              >
                {tr("取消")}
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={submitting}
                disabled={submitting}
              >
                {editingUser ? tr("保存用户") : tr("创建用户")}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </AppModal>
    </div>
  );
}
