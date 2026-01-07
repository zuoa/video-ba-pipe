import React from 'react';
import { Pagination, Space, Card, Typography } from 'antd';

const { Text } = Typography;

interface PaginationBarProps {
  current: number;
  pageSize: number;
  total: number;
  pageSizeOptions?: string[];
  onChange: (page: number, pageSize?: number) => void;
  showSizeChanger?: boolean;
  position?: 'top' | 'bottom';
}

const PaginationBar: React.FC<PaginationBarProps> = ({
  current,
  pageSize,
  total,
  pageSizeOptions = ['12', '20', '48', '100'],
  onChange,
  showSizeChanger = true,
  position = 'bottom',
}) => {
  return (
    <Card
      style={{
        marginBottom: position === 'top' ? 16 : 0,
        marginTop: position === 'bottom' ? 16 : 0,
      }}
    >
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary">
          共 {total} 条记录，当前第 {current} 页
        </Text>
        <Pagination
          current={current}
          pageSize={pageSize}
          total={total}
          onChange={onChange}
          showSizeChanger={showSizeChanger}
          pageSizeOptions={pageSizeOptions.map(s => parseInt(s))}
          showTotal={false}
        />
      </Space>
    </Card>
  );
};

export default PaginationBar;
