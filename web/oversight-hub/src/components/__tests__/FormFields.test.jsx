import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import FormFields from '../tasks/FormFields';

describe('FormFields Component', () => {
  const mockOnChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when fields array is empty', () => {
    const { container } = render(
      <FormFields fields={[]} values={{}} onChange={mockOnChange} />
    );
    // The outer Box is always rendered but contains no field elements
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  describe('Text fields', () => {
    it('renders a text input with the correct label', () => {
      const fields = [{ name: 'title', label: 'Title', type: 'text' }];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      expect(screen.getByLabelText('Title')).toBeInTheDocument();
    });

    it('displays the current value in a text field', () => {
      const fields = [{ name: 'title', label: 'Title', type: 'text' }];
      render(
        <FormFields
          fields={fields}
          values={{ title: 'My Title' }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByDisplayValue('My Title')).toBeInTheDocument();
    });

    it('calls onChange with field name and new value when text changes', () => {
      const fields = [{ name: 'title', label: 'Title', type: 'text' }];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      const input = screen.getByLabelText('Title');
      fireEvent.change(input, { target: { value: 'New Value' } });
      expect(mockOnChange).toHaveBeenCalledWith({ title: 'New Value' });
    });

    it('displays error message when field has an error', () => {
      const fields = [{ name: 'title', label: 'Title', type: 'text' }];
      render(
        <FormFields
          fields={fields}
          values={{}}
          onChange={mockOnChange}
          errors={{ title: 'Title is required' }}
        />
      );
      expect(screen.getByText('Title is required')).toBeInTheDocument();
    });

    it('displays field description as helper text when no error', () => {
      const fields = [
        {
          name: 'title',
          label: 'Title',
          type: 'text',
          description: 'Enter a descriptive title',
        },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      expect(screen.getByText('Enter a descriptive title')).toBeInTheDocument();
    });

    it('uses defaultValue when values does not contain the field', () => {
      const fields = [
        {
          name: 'title',
          label: 'Title',
          type: 'text',
          defaultValue: 'Default Title',
        },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      expect(screen.getByDisplayValue('Default Title')).toBeInTheDocument();
    });
  });

  describe('Number fields', () => {
    it('renders a number input', () => {
      const fields = [{ name: 'count', label: 'Count', type: 'number' }];
      render(
        <FormFields fields={fields} values={{ count: 5 }} onChange={mockOnChange} />
      );
      expect(screen.getByDisplayValue('5')).toBeInTheDocument();
    });

    it('calls onChange when number input changes', () => {
      const fields = [{ name: 'count', label: 'Count', type: 'number' }];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      const input = screen.getByLabelText('Count');
      fireEvent.change(input, { target: { value: '42' } });
      expect(mockOnChange).toHaveBeenCalledWith({ count: '42' });
    });
  });

  describe('Textarea fields', () => {
    it('renders a multiline textarea', () => {
      const fields = [
        { name: 'description', label: 'Description', type: 'textarea' },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      // MUI multiline TextField renders a textarea element
      expect(screen.getByLabelText('Description')).toBeInTheDocument();
    });

    it('calls onChange when textarea changes', () => {
      const fields = [
        { name: 'description', label: 'Description', type: 'textarea' },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      const textarea = screen.getByLabelText('Description');
      fireEvent.change(textarea, { target: { value: 'Some text' } });
      expect(mockOnChange).toHaveBeenCalledWith({ description: 'Some text' });
    });
  });

  describe('Checkbox fields', () => {
    it('renders a checkbox with label', () => {
      const fields = [
        { name: 'enabled', label: 'Enable Feature', type: 'checkbox' },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ enabled: false }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByText('Enable Feature')).toBeInTheDocument();
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    it('renders checkbox as checked when value is true', () => {
      const fields = [
        { name: 'enabled', label: 'Enable Feature', type: 'checkbox' },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ enabled: true }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByRole('checkbox')).toBeChecked();
    });

    it('calls onChange with boolean when checkbox is toggled', () => {
      const fields = [
        { name: 'enabled', label: 'Enable Feature', type: 'checkbox' },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ enabled: false }}
          onChange={mockOnChange}
        />
      );
      const checkbox = screen.getByRole('checkbox');
      fireEvent.click(checkbox);
      expect(mockOnChange).toHaveBeenCalledWith({ enabled: true });
    });

    it('shows description under checkbox label', () => {
      const fields = [
        {
          name: 'enabled',
          label: 'Enable Feature',
          type: 'checkbox',
          description: 'Activates the feature',
        },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      expect(screen.getByText('Activates the feature')).toBeInTheDocument();
    });
  });

  describe('Range/Slider fields', () => {
    it('renders a slider with label showing current value', () => {
      const fields = [
        {
          name: 'quality',
          label: 'Quality',
          type: 'range',
          min: 0,
          max: 100,
          step: 10,
        },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ quality: 50 }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByText(/Quality:/)).toBeInTheDocument();
      expect(screen.getByText(/50%/)).toBeInTheDocument();
    });

    it('shows description for range fields', () => {
      const fields = [
        {
          name: 'quality',
          label: 'Quality',
          type: 'range',
          min: 0,
          max: 100,
          step: 10,
          description: 'Content quality threshold',
        },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ quality: 50 }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByText('Content quality threshold')).toBeInTheDocument();
    });
  });

  describe('Select fields', () => {
    it('renders a select with options', () => {
      const fields = [
        {
          name: 'category',
          label: 'Category',
          type: 'select',
          options: ['technology', 'finance', 'health'],
        },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      // MUI Select renders as a combobox
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    });

    it('shows description as helper text for select fields', () => {
      const fields = [
        {
          name: 'category',
          label: 'Category',
          type: 'select',
          options: ['tech'],
          description: 'Choose a category',
        },
      ];
      render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      expect(screen.getByText('Choose a category')).toBeInTheDocument();
    });
  });

  describe('Unknown field types', () => {
    it('returns null for unknown field types (no crash)', () => {
      const fields = [{ name: 'weird', label: 'Weird', type: 'unknown_type' }];
      const { container } = render(
        <FormFields fields={fields} values={{}} onChange={mockOnChange} />
      );
      // Should render the outer Box but nothing for the unknown field
      expect(screen.queryByLabelText('Weird')).not.toBeInTheDocument();
    });
  });

  describe('Multiple fields', () => {
    it('renders multiple fields of different types', () => {
      const fields = [
        { name: 'title', label: 'Title', type: 'text' },
        { name: 'enabled', label: 'Enabled', type: 'checkbox' },
      ];
      render(
        <FormFields
          fields={fields}
          values={{ title: 'Hello', enabled: false }}
          onChange={mockOnChange}
        />
      );
      expect(screen.getByDisplayValue('Hello')).toBeInTheDocument();
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });
  });
});
