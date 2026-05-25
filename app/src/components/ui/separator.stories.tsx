import type { Meta, StoryObj } from '@storybook/react-vite';
import { Separator } from './separator';

const meta = {
  title: 'UI/Separator',
  component: Separator,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Separator>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Horizontal: Story = {
  render: () => (
    <div className="w-[260px]">
      <div className="space-y-1">
        <h4 className="text-sm font-medium">Voice profile</h4>
        <p className="text-sm text-muted-foreground">Narrator · 12 min of reference</p>
      </div>
      <Separator className="my-4" />
      <div className="flex h-5 items-center gap-4 text-sm">
        <span>Edit</span>
        <Separator orientation="vertical" />
        <span>Duplicate</span>
        <Separator orientation="vertical" />
        <span>Delete</span>
      </div>
    </div>
  ),
};
