import type { Meta, StoryObj } from '@storybook/react-vite';
import { Input } from './input';

const meta = {
  title: 'UI/Input',
  component: Input,
  tags: ['autodocs'],
  argTypes: {
    type: {
      control: 'select',
      options: ['text', 'email', 'password', 'number', 'search', 'url'],
    },
  },
  args: { placeholder: 'Type something', disabled: false, type: 'text' },
} satisfies Meta<typeof Input>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Disabled: Story = { args: { disabled: true } };
export const WithValue: Story = { args: { defaultValue: 'Narrator' } };
export const Email: Story = { args: { type: 'email', placeholder: 'you@example.com' } };
export const Password: Story = { args: { type: 'password', placeholder: '••••••••' } };
