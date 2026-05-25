import type { Meta, StoryObj } from '@storybook/react-vite';
import { Pencil, Trash2, X } from 'lucide-react';
import { CircleButton } from './circle-button';

const meta = {
  title: 'UI/CircleButton',
  component: CircleButton,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
  args: { icon: X, 'aria-label': 'Close' },
} satisfies Meta<typeof CircleButton>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Disabled: Story = { args: { disabled: true } };

export const Variants: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <CircleButton icon={X} aria-label="Close" />
      <CircleButton icon={Pencil} aria-label="Edit" />
      <CircleButton icon={Trash2} aria-label="Delete" />
    </div>
  ),
};
