import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { Button } from './button';
import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from './toast';

const meta = {
  title: 'UI/Toast',
  component: Toast,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof Toast>;

export default meta;

type Story = StoryObj<typeof meta>;

const ToastDemo = ({ variant }: { variant?: 'default' | 'destructive' }) => {
  const [open, setOpen] = useState(true);

  return (
    <ToastProvider swipeDirection="right">
      <Button variant="outline" onClick={() => setOpen(true)}>
        Show toast
      </Button>
      <Toast open={open} onOpenChange={setOpen} variant={variant}>
        <div className="grid gap-1">
          <ToastTitle>Profile saved</ToastTitle>
          <ToastDescription>Your changes are now available everywhere in the app.</ToastDescription>
        </div>
        <ToastAction altText="Undo save">Undo</ToastAction>
        <ToastClose />
      </Toast>
      <ToastViewport />
    </ToastProvider>
  );
};

export const Default: Story = {
  render: () => <ToastDemo />,
};

export const Destructive: Story = {
  render: () => <ToastDemo variant="destructive" />,
};
