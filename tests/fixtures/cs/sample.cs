using System;

namespace SampleApp
{
    public class SampleService
    {
        public string GetName()
        {
            return "Sample";
        }

        public void LogMessage(string message)
        {
            Console.WriteLine(message);
        }
    }
}
