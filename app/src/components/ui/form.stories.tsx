import type { Meta, StoryObj } from '@storybook/react-vite';
import { useForm } from 'react-hook-form';
import { Button } from './button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from './form';
import { Input } from './input';

type Values = { displayName: string };

const FormExample = () => {
  const form = useForm<Values>({
    defaultValues: { displayName: '' },
  });

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(() => {})}
        className="w-[360px] space-y-4"
      >
        <FormField
          control={form.control}
          name="displayName"
          rules={{ required: 'Display name is required' }}
          render={({ field }) => (
            <FormItem>
              <FormLabel>Display name</FormLabel>
              <FormControl>
                <Input placeholder="Narrator" {...field} />
              </FormControl>
              <FormDescription>Shown next to clips in the library.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Save</Button>
      </form>
    </Form>
  );
};

const meta = {
  title: 'UI/Form',
  component: FormExample,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof FormExample>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};
